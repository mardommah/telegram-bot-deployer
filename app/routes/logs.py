import asyncio
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import get_current_user
from app.database import get_db
from app.models import Bot
from app.docker_manager import DockerManager
from app.deploy_worker import get_job, DeployPhase

router = APIRouter()


@router.get("/bots/{bot_id}/logs/stream")
async def stream_logs(request: Request, bot_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot or not bot.container_id:
        return EventSourceResponse(iter([]))

    async def event_generator():
        try:
            for line in DockerManager.stream_logs(bot.container_id):
                if await request.is_disconnected():
                    break
                yield {"data": line.rstrip()}
                await asyncio.sleep(0)
        except Exception:
            yield {"data": "[stream ended]"}

    return EventSourceResponse(event_generator())


@router.get("/bots/{bot_id}/deploy/stream")
async def stream_deploy(request: Request, bot_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    async def event_generator():
        offset = 0
        while True:
            if await request.is_disconnected():
                break

            job = get_job(bot_id)
            if not job:
                yield {
                    "event": "status",
                    "data": json.dumps({"phase": "not_found", "progress": 0}),
                }
                break

            # Send new log lines
            new_lines = job.get_logs_from(offset)
            for line in new_lines:
                yield {"event": "log", "data": line}
                offset += 1

            # Send status update
            yield {
                "event": "status",
                "data": json.dumps({
                    "phase": job.phase.value,
                    "progress": job.progress,
                    "error": job.error,
                    "container_id": job.container_id[:12] if job.container_id else "",
                }),
            }

            # Stop streaming when done or failed
            if job.phase in (DeployPhase.DONE, DeployPhase.FAILED):
                break

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())
