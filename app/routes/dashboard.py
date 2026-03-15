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
    bots = result.scalars().all()

    # Sync statuses from Docker
    for bot in bots:
        if bot.container_id:
            docker_status = DockerManager.get_status(bot.container_id)
            if docker_status == "running":
                bot.status = "running"
            elif docker_status == "exited":
                bot.status = "stopped"
            elif docker_status == "not_found":
                bot.status = "error"
                bot.container_id = None
        await db.commit()

    return templates.TemplateResponse("dashboard.html", {"request": request, "bots": bots, "user": user})
