import docker
from docker.errors import NotFound, APIError
from pathlib import Path
from app.config import UPLOAD_DIR

client = docker.from_env()

DOCKERFILE_TEMPLATE = """FROM python:3.11-slim
WORKDIR /bot
{install_deps}
COPY . .
CMD ["python", "-u", "{entrypoint}"]
"""


class DockerManager:
    @staticmethod
    def _bot_image_name(bot_name: str) -> str:
        return f"tgbot-{bot_name}".lower().replace(" ", "-")

    @staticmethod
    def _bot_container_name(bot_name: str) -> str:
        return f"tgbot-{bot_name}".lower().replace(" ", "-")

    @staticmethod
    def deploy_bot(bot_name: str, filename: str, telegram_token: str) -> str:
        bot_dir = UPLOAD_DIR / bot_name
        filepath = bot_dir / filename

        # Check for requirements.txt
        req_file = bot_dir / "requirements.txt"
        if req_file.exists():
            install_deps = "COPY requirements.txt requirements.txt\nRUN pip install --no-cache-dir -r requirements.txt"
        else:
            install_deps = ""

        dockerfile_content = DOCKERFILE_TEMPLATE.format(
            entrypoint=filename, install_deps=install_deps
        )
        dockerfile_path = bot_dir / "Dockerfile"
        dockerfile_path.write_text(dockerfile_content)

        image_name = DockerManager._bot_image_name(bot_name)
        container_name = DockerManager._bot_container_name(bot_name)

        # Remove old container if exists
        try:
            old = client.containers.get(container_name)
            old.remove(force=True)
        except NotFound:
            pass

        # Build image
        client.images.build(path=str(bot_dir), tag=image_name, rm=True)

        # Run container
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
            container = client.containers.get(container_id)
            container.stop(timeout=10)
        except NotFound:
            pass

    @staticmethod
    def start_bot(container_id: str):
        container = client.containers.get(container_id)
        container.start()

    @staticmethod
    def restart_bot(container_id: str):
        container = client.containers.get(container_id)
        container.restart(timeout=10)

    @staticmethod
    def remove_bot(container_id: str, bot_name: str):
        try:
            container = client.containers.get(container_id)
            container.remove(force=True)
        except NotFound:
            pass
        # Remove image
        image_name = DockerManager._bot_image_name(bot_name)
        try:
            client.images.remove(image_name, force=True)
        except Exception:
            pass

    @staticmethod
    def get_logs(container_id: str, tail: int = 100) -> str:
        try:
            container = client.containers.get(container_id)
            return container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
        except NotFound:
            return "Container not found."

    @staticmethod
    def stream_logs(container_id: str):
        try:
            container = client.containers.get(container_id)
            for line in container.logs(stream=True, follow=True, timestamps=True):
                yield line.decode("utf-8", errors="replace")
        except NotFound:
            yield "Container not found.\n"

    @staticmethod
    def get_status(container_id: str) -> str:
        try:
            container = client.containers.get(container_id)
            return container.status  # running, exited, paused, etc.
        except NotFound:
            return "not_found"
        except APIError:
            return "error"
