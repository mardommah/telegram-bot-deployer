import threading
import time
from dataclasses import dataclass, field
from enum import Enum


class DeployPhase(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    BUILDING = "building"
    STARTING = "starting"
    DONE = "done"
    FAILED = "failed"


@dataclass
class DeployJob:
    bot_id: int
    phase: DeployPhase = DeployPhase.QUEUED
    logs: list[str] = field(default_factory=list)
    progress: int = 0  # 0-100
    error: str = ""
    container_id: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append_log(self, msg: str):
        with self._lock:
            self.logs.append(msg)

    def get_logs_from(self, offset: int) -> list[str]:
        with self._lock:
            return self.logs[offset:]


# Global registry of active/recent deploy jobs keyed by bot_id
_jobs: dict[int, DeployJob] = {}
_jobs_lock = threading.Lock()


def get_job(bot_id: int) -> DeployJob | None:
    with _jobs_lock:
        return _jobs.get(bot_id)


def start_deploy(bot_id: int, bot_name: str, filename: str, telegram_token: str) -> DeployJob:
    job = DeployJob(bot_id=bot_id)
    with _jobs_lock:
        _jobs[bot_id] = job

    t = threading.Thread(
        target=_run_deploy,
        args=(job, bot_name, filename, telegram_token),
        daemon=True,
    )
    t.start()
    return job


def _update_bot_in_db(bot_id: int, status: str, container_id: str = None):
    """Update bot record in DB from the background thread using a sync connection."""
    try:
        from sqlalchemy import create_engine, text
        from app.config import DATABASE_URL

        # Convert async URL to sync for this thread
        sync_url = DATABASE_URL.replace("sqlite+aiosqlite", "sqlite")
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            if container_id is not None:
                conn.execute(
                    text("UPDATE bots SET status = :status, container_id = :cid WHERE id = :id"),
                    {"status": status, "cid": container_id, "id": bot_id},
                )
            else:
                conn.execute(
                    text("UPDATE bots SET status = :status WHERE id = :id"),
                    {"status": status, "id": bot_id},
                )
            conn.commit()
        engine.dispose()
    except Exception as e:
        # Log but don't crash the deploy thread
        print(f"[deploy_worker] DB update failed for bot {bot_id}: {e}")


def _run_deploy(job: DeployJob, bot_name: str, filename: str, telegram_token: str):
    try:
        import docker
        from docker.errors import NotFound
        from app.docker_manager import DockerManager, DOCKERFILE_TEMPLATE
        from app.config import UPLOAD_DIR

        client = docker.from_env(timeout=60)

        bot_dir = UPLOAD_DIR / bot_name
        image_name = DockerManager._bot_image_name(bot_name)
        container_name = DockerManager._bot_container_name(bot_name)

        # Phase 1: Prepare Dockerfile
        job.phase = DeployPhase.PREPARING
        job.progress = 5
        job.append_log(f"[prepare] Generating Dockerfile for '{bot_name}'...")

        req_file = bot_dir / "requirements.txt"
        if req_file.exists():
            install_deps = "COPY requirements.txt requirements.txt\nRUN pip install --no-cache-dir -r requirements.txt"
            job.append_log("[prepare] Found requirements.txt — will install dependencies")
        else:
            install_deps = ""
            job.append_log("[prepare] No requirements.txt found — skipping pip install")

        dockerfile_content = DOCKERFILE_TEMPLATE.format(
            entrypoint=filename, install_deps=install_deps
        )
        (bot_dir / "Dockerfile").write_text(dockerfile_content)
        job.append_log(f"[prepare] Entrypoint: {filename}")
        job.progress = 10

        # Phase 2: Cleanup old container
        job.append_log("[prepare] Checking for existing container...")
        try:
            old = client.containers.get(container_name)
            job.append_log(f"[prepare] Removing old container {container_name}...")
            old.remove(force=True)
            job.append_log("[prepare] Old container removed")
        except NotFound:
            job.append_log("[prepare] No existing container found")
        job.progress = 15

        # Phase 3: Build image
        job.phase = DeployPhase.BUILDING
        job.append_log(f"[build] Building image '{image_name}'...")
        job.progress = 20

        resp = client.api.build(
            path=str(bot_dir),
            tag=image_name,
            rm=True,
            decode=True,
        )

        for chunk in resp:
            if "stream" in chunk:
                line = chunk["stream"].rstrip()
                if line:
                    job.append_log(f"[build] {line}")
            if "error" in chunk:
                raise RuntimeError(chunk["error"].strip())
            if "errorDetail" in chunk:
                detail = chunk["errorDetail"].get("message", "Unknown build error")
                raise RuntimeError(detail)

            # Estimate progress during build (20-80)
            if job.progress < 80:
                job.progress = min(job.progress + 1, 80)

        job.progress = 85
        job.append_log("[build] Image built successfully")

        # Phase 4: Start container
        job.phase = DeployPhase.STARTING
        job.append_log(f"[start] Creating container '{container_name}'...")
        job.progress = 90

        container = client.containers.run(
            image_name,
            name=container_name,
            environment={"TELEGRAM_BOT_TOKEN": telegram_token},
            detach=True,
            restart_policy={"Name": "unless-stopped"},
        )

        job.container_id = container.id
        job.progress = 95
        job.append_log(f"[start] Container started: {container.short_id}")

        # Brief wait to check it didn't crash immediately
        time.sleep(2)
        container.reload()
        if container.status == "running":
            job.append_log("[done] Bot is running!")
        else:
            job.append_log(f"[warn] Container status: {container.status}")

        job.phase = DeployPhase.DONE
        job.progress = 100
        job.append_log("[done] Deploy completed successfully")

        # Persist result to DB
        _update_bot_in_db(job.bot_id, "running", container.id)

    except Exception as e:
        job.phase = DeployPhase.FAILED
        job.error = str(e)
        job.append_log(f"[error] Deploy failed: {e}")

        # Persist failure to DB
        _update_bot_in_db(job.bot_id, "error")
