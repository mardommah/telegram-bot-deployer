import re
import docker
from docker.errors import NotFound, APIError

from app.config import UPLOAD_DIR

_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_/][a-zA-Z0-9_./-]*\.py$")

DOCKERFILE_TEMPLATE = """FROM python:3.11-slim
WORKDIR /bot
{install_deps}
COPY . .
CMD ["python", "-u", "{entrypoint}"]
"""


def _get_client():
    """Lazy Docker client — only connects when actually needed."""
    return docker.from_env(timeout=30)


class DockerManager:
    @staticmethod
    def _bot_image_name(bot_name: str) -> str:
        return f"tgbot-{bot_name}".lower().replace(" ", "-")

    @staticmethod
    def _bot_container_name(bot_name: str) -> str:
        return f"tgbot-{bot_name}".lower().replace(" ", "-")

    @staticmethod
    def deploy_bot(bot_name: str, filename: str, telegram_token: str) -> str:
        if not _SAFE_FILENAME_RE.match(filename):
            raise ValueError(f"Unsafe entrypoint filename: {filename}")

        client = _get_client()
        bot_dir = UPLOAD_DIR / bot_name

        req_file = bot_dir / "requirements.txt"
        if req_file.exists():
            install_deps = "COPY requirements.txt requirements.txt\nRUN pip install --no-cache-dir -r requirements.txt"
        else:
            install_deps = ""

        dockerfile_content = DOCKERFILE_TEMPLATE.format(
            entrypoint=filename, install_deps=install_deps
        )
        (bot_dir / "Dockerfile").write_text(dockerfile_content)

        image_name = DockerManager._bot_image_name(bot_name)
        container_name = DockerManager._bot_container_name(bot_name)

        try:
            old = client.containers.get(container_name)
            old.remove(force=True)
        except NotFound:
            pass

        client.images.build(path=str(bot_dir), tag=image_name, rm=True)

        container = client.containers.run(
            image_name,
            name=container_name,
            environment={"TELEGRAM_BOT_TOKEN": telegram_token},
            detach=True,
            restart_policy={"Name": "unless-stopped"},
        )
        return container.id

    @staticmethod
    def stop_bot(container_id: str):
        try:
            client = _get_client()
            container = client.containers.get(container_id)
            container.stop(timeout=10)
        except NotFound:
            pass

    @staticmethod
    def start_bot(container_id: str):
        client = _get_client()
        container = client.containers.get(container_id)
        container.start()

    @staticmethod
    def restart_bot(container_id: str):
        client = _get_client()
        container = client.containers.get(container_id)
        container.restart(timeout=10)

    @staticmethod
    def remove_bot(container_id: str, bot_name: str):
        client = _get_client()
        try:
            container = client.containers.get(container_id)
            container.remove(force=True)
        except NotFound:
            pass
        image_name = DockerManager._bot_image_name(bot_name)
        try:
            client.images.remove(image_name, force=True)
        except Exception:
            pass

    @staticmethod
    def get_logs(container_id: str, tail: int = 100) -> str:
        try:
            client = _get_client()
            container = client.containers.get(container_id)
            return container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
        except NotFound:
            return "Container not found."

    @staticmethod
    def stream_logs(container_id: str):
        try:
            client = _get_client()
            container = client.containers.get(container_id)
            for line in container.logs(stream=True, follow=True, timestamps=True):
                yield line.decode("utf-8", errors="replace")
        except NotFound:
            yield "Container not found.\n"

    @staticmethod
    def get_status(container_id: str) -> str:
        try:
            client = _get_client()
            container = client.containers.get(container_id)
            return container.status
        except NotFound:
            return "not_found"
        except APIError:
            return "error"
