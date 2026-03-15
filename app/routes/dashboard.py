import asyncio
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.auth import get_current_user
from app.database import get_db
from app.models import Bot
from app.docker_manager import DockerManager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    result = await db.execute(select(Bot).order_by(Bot.created_at.desc()))
    bots = [row[0] for row in result.all()]

    # Sync statuses from Docker (non-blocking)
    loop = asyncio.get_event_loop()
    dirty = False
    for bot in bots:
        if not bot or not bot.container_id:
            continue
        try:
            docker_status = await loop.run_in_executor(
                None, DockerManager.get_status, bot.container_id
            )
            if docker_status == "running":
                bot.status = "running"
            elif docker_status == "exited":
                bot.status = "stopped"
            elif docker_status == "not_found":
                bot.status = "error"
                bot.container_id = None
            dirty = True
        except Exception:
            pass

    if dirty:
        await db.commit()

    bots = [b for b in bots if b is not None]

    return templates.TemplateResponse("dashboard.html", {"request": request, "bots": bots, "user": user})
