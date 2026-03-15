import shutil
import zipfile
from pathlib import Path
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.auth import get_current_user
from app.database import get_db
from app.models import Bot
from app.config import fernet, UPLOAD_DIR
from app.docker_manager import DockerManager
from app.deploy_worker import start_deploy, get_job, DeployPhase

router = APIRouter(prefix="/bots")
templates = Jinja2Templates(directory="app/templates")


def _require_login(request: Request):
    user = get_current_user(request)
    if not user:
        raise Exception("Not authenticated")
    return user


@router.get("/create", response_class=HTMLResponse)
async def create_form(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("bot_create.html", {"request": request, "error": None})


@router.post("/create")
async def create_bot(
    request: Request,
    name: str = Form(...),
    telegram_token: str = Form(...),
    entrypoint: str = Form(""),
    bot_file: UploadFile = File(...),
    requirements_file: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    # Check unique name
    existing = await db.execute(select(Bot).where(Bot.name == name))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            "bot_create.html", {"request": request, "error": f"Bot '{name}' already exists"}
        )

    bot_dir = UPLOAD_DIR / name
    bot_dir.mkdir(parents=True, exist_ok=True)

    original_filename = bot_file.filename or "bot.py"
    content = await bot_file.read()
    is_zip = original_filename.lower().endswith(".zip")

    if is_zip:
        # Save and extract zip
        zip_path = bot_dir / original_filename
        zip_path.write_bytes(content)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Security: reject paths with .. or absolute paths
                for member in zf.namelist():
                    if member.startswith("/") or ".." in member:
                        shutil.rmtree(bot_dir)
                        return templates.TemplateResponse(
                            "bot_create.html",
                            {"request": request, "error": "Zip contains unsafe paths"},
                        )
                zf.extractall(bot_dir)
        except zipfile.BadZipFile:
            shutil.rmtree(bot_dir)
            return templates.TemplateResponse(
                "bot_create.html", {"request": request, "error": "Invalid zip file"}
            )
        zip_path.unlink()

        # Detect entrypoint
        filename = _resolve_entrypoint(bot_dir, entrypoint)
        if not filename:
            shutil.rmtree(bot_dir)
            return templates.TemplateResponse(
                "bot_create.html",
                {
                    "request": request,
                    "error": "Could not detect entrypoint. Specify it manually (e.g. main.py or src/bot.py)",
                },
            )
    else:
        # Single .py file
        filename = original_filename
        (bot_dir / filename).write_bytes(content)

    # Save requirements.txt if provided (only if not already in zip)
    if requirements_file and requirements_file.filename:
        req_content = await requirements_file.read()
        if req_content.strip():
            (bot_dir / "requirements.txt").write_bytes(req_content)

    # Encrypt token
    encrypted_token = fernet.encrypt(telegram_token.encode()).decode()

    bot = Bot(name=name, telegram_token=encrypted_token, filename=filename, status="created")
    db.add(bot)
    await db.commit()
    await db.refresh(bot)

    return RedirectResponse(f"/bots/{bot.id}", status_code=303)


def _resolve_entrypoint(bot_dir: Path, user_entrypoint: str) -> str | None:
    """Resolve the entrypoint .py file from extracted zip contents."""
    # User specified entrypoint
    if user_entrypoint.strip():
        ep = user_entrypoint.strip()
        if (bot_dir / ep).is_file():
            return ep
        return None

    # Auto-detect: check common entrypoint names
    candidates = ["main.py", "bot.py", "app.py", "run.py"]
    for name in candidates:
        if (bot_dir / name).is_file():
            return name

    # Check one level of subdirectories (e.g. zip extracted with a root folder)
    subdirs = [d for d in bot_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if len(subdirs) == 1:
        sub = subdirs[0]
        for name in candidates:
            if (sub / name).is_file():
                return f"{sub.name}/{name}"

    # Fallback: first .py file found at root
    py_files = sorted(bot_dir.glob("*.py"))
    if py_files:
        return py_files[0].name

    return None


@router.get("/{bot_id}", response_class=HTMLResponse)
async def bot_detail(request: Request, bot_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot:
        return RedirectResponse("/", status_code=303)

    # Sync deploy job result to DB
    job = get_job(bot_id)
    if job and job.phase == DeployPhase.DONE and job.container_id:
        bot.container_id = job.container_id
        bot.status = "running"
        await db.commit()
    elif job and job.phase == DeployPhase.FAILED:
        bot.status = "error"
        await db.commit()

    deploying = job and job.phase not in (DeployPhase.DONE, DeployPhase.FAILED)

    # Sync status from Docker
    logs = ""
    if not deploying and bot.container_id:
        docker_status = DockerManager.get_status(bot.container_id)
        if docker_status == "running":
            bot.status = "running"
        elif docker_status == "exited":
            bot.status = "stopped"
        elif docker_status == "not_found":
            bot.status = "error"
        await db.commit()
        logs = DockerManager.get_logs(bot.container_id)

    return templates.TemplateResponse(
        "bot_detail.html", {"request": request, "bot": bot, "logs": logs, "deploying": deploying}
    )


@router.post("/{bot_id}/deploy")
async def deploy_bot(request: Request, bot_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot:
        return RedirectResponse("/", status_code=303)

    # Don't start if already deploying
    existing_job = get_job(bot_id)
    if existing_job and existing_job.phase not in (DeployPhase.DONE, DeployPhase.FAILED):
        return RedirectResponse(f"/bots/{bot.id}", status_code=303)

    token = fernet.decrypt(bot.telegram_token.encode()).decode()
    bot.status = "deploying"
    await db.commit()

    start_deploy(bot.id, bot.name, bot.filename, token)

    return RedirectResponse(f"/bots/{bot.id}", status_code=303)


@router.post("/{bot_id}/stop")
async def stop_bot(request: Request, bot_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot or not bot.container_id:
        return RedirectResponse(f"/bots/{bot_id}", status_code=303)

    DockerManager.stop_bot(bot.container_id)
    bot.status = "stopped"
    await db.commit()
    return RedirectResponse(f"/bots/{bot.id}", status_code=303)


@router.post("/{bot_id}/start")
async def start_bot(request: Request, bot_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot or not bot.container_id:
        return RedirectResponse(f"/bots/{bot_id}", status_code=303)

    DockerManager.start_bot(bot.container_id)
    bot.status = "running"
    await db.commit()
    return RedirectResponse(f"/bots/{bot.id}", status_code=303)


@router.post("/{bot_id}/restart")
async def restart_bot(request: Request, bot_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot or not bot.container_id:
        return RedirectResponse(f"/bots/{bot_id}", status_code=303)

    DockerManager.restart_bot(bot.container_id)
    bot.status = "running"
    await db.commit()
    return RedirectResponse(f"/bots/{bot.id}", status_code=303)


@router.post("/{bot_id}/delete")
async def delete_bot(request: Request, bot_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot:
        return RedirectResponse("/", status_code=303)

    if bot.container_id:
        DockerManager.remove_bot(bot.container_id, bot.name)

    # Remove uploaded files
    bot_dir = UPLOAD_DIR / bot.name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)

    await db.delete(bot)
    await db.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/{bot_id}/status", response_class=HTMLResponse)
async def bot_status_partial(request: Request, bot_id: int, db: AsyncSession = Depends(get_db)):
    """HTMX partial: returns just the status badge for polling."""
    bot = await db.get(Bot, bot_id)
    if not bot:
        return HTMLResponse("<span>unknown</span>")

    # Sync deploy job result
    job = get_job(bot_id)
    if job and job.phase == DeployPhase.DONE and job.container_id:
        bot.container_id = job.container_id
        bot.status = "running"
        await db.commit()
    elif job and job.phase == DeployPhase.FAILED:
        bot.status = "error"
        await db.commit()
    elif job and job.phase not in (DeployPhase.DONE, DeployPhase.FAILED):
        bot.status = "deploying"
    elif bot.container_id:
        docker_status = DockerManager.get_status(bot.container_id)
        if docker_status == "running":
            bot.status = "running"
        elif docker_status == "exited":
            bot.status = "stopped"
        elif docker_status == "not_found":
            bot.status = "error"
        await db.commit()

    return templates.TemplateResponse("partials/bot_status.html", {"request": request, "bot": bot})
