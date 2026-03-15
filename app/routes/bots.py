import asyncio
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


async def _get_docker_status(container_id: str) -> str:
    """Run sync Docker status check in a thread to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, DockerManager.get_status, container_id)


async def _get_docker_logs(container_id: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, DockerManager.get_logs, container_id)

router = APIRouter(prefix="/bots")
templates = Jinja2Templates(directory="app/templates")


import re
import os

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,98}[a-zA-Z0-9]$")


def _validate_bot_name(name: str) -> str | None:
    """Return error message if name is invalid, None if OK."""
    if not name or len(name) < 2 or len(name) > 100:
        return "Bot name must be 2-100 characters"
    if not _SAFE_NAME_RE.match(name):
        return "Bot name can only contain letters, numbers, hyphens, and underscores"
    return None


def _safe_zip_extract(zf: zipfile.ZipFile, dest: Path) -> str | None:
    """Extract zip safely. Returns error message or None on success."""
    for member in zf.infolist():
        # Reject symlinks
        if member.is_dir():
            continue
        # Normalize and check path traversal
        target = (dest / member.filename).resolve()
        if not str(target).startswith(str(dest.resolve())):
            return "Zip contains path traversal"
        # Reject symlinks (external_attr check)
        if (member.external_attr >> 16) & 0o120000 == 0o120000:
            return "Zip contains symbolic links"
    # Check total uncompressed size (max 500MB)
    total_size = sum(m.file_size for m in zf.infolist())
    if total_size > 500 * 1024 * 1024:
        return "Zip contents exceed 500MB limit"
    # Check file count (max 1000)
    if len(zf.infolist()) > 1000:
        return "Zip contains too many files (max 1000)"
    zf.extractall(dest)
    return None


def _validate_entrypoint(bot_dir: Path, ep: str) -> bool:
    """Validate entrypoint path is safe and within bot_dir."""
    if not ep or not ep.endswith(".py"):
        return False
    target = (bot_dir / ep).resolve()
    return str(target).startswith(str(bot_dir.resolve())) and target.is_file()


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

    # Validate bot name
    name_err = _validate_bot_name(name)
    if name_err:
        return templates.TemplateResponse(
            "bot_create.html", {"request": request, "error": name_err}
        )

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
        zip_path = bot_dir / original_filename
        zip_path.write_bytes(content)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                extract_err = _safe_zip_extract(zf, bot_dir)
                if extract_err:
                    shutil.rmtree(bot_dir)
                    return templates.TemplateResponse(
                        "bot_create.html",
                        {"request": request, "error": extract_err},
                    )
        except zipfile.BadZipFile:
            shutil.rmtree(bot_dir)
            return templates.TemplateResponse(
                "bot_create.html", {"request": request, "error": "Invalid zip file"}
            )
        zip_path.unlink()

        # Detect entrypoint
        filename = _resolve_entrypoint(bot_dir, entrypoint)
        if not filename or not _validate_entrypoint(bot_dir, filename):
            shutil.rmtree(bot_dir)
            return templates.TemplateResponse(
                "bot_create.html",
                {
                    "request": request,
                    "error": "Could not detect entrypoint. Specify a .py file (e.g. main.py or src/bot.py)",
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
    await db.flush()  # generates bot.id
    await db.commit()

    return RedirectResponse(f"/bots/{bot.id}", status_code=303)


def _resolve_entrypoint(bot_dir: Path, user_entrypoint: str) -> str | None:
    """Resolve the entrypoint .py file from extracted zip contents."""
    # User specified entrypoint
    if user_entrypoint.strip():
        ep = user_entrypoint.strip()
        if _validate_entrypoint(bot_dir, ep):
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
    deploying = False

    if job and job.phase == DeployPhase.DONE and job.container_id:
        bot.container_id = job.container_id
        bot.status = "running"
        await db.commit()
    elif job and job.phase == DeployPhase.FAILED:
        bot.status = "error"
        await db.commit()
    elif job and job.phase not in (DeployPhase.DONE, DeployPhase.FAILED):
        deploying = True
    elif bot.status == "deploying" and not job:
        # Stale state: server restarted or thread died, no active job
        bot.status = "error"
        await db.commit()

    # Sync status from Docker
    logs = ""
    if not deploying and bot.container_id:
        docker_status = await _get_docker_status(bot.container_id)
        if docker_status == "running":
            bot.status = "running"
        elif docker_status == "exited":
            bot.status = "stopped"
        elif docker_status == "not_found":
            bot.status = "error"
        await db.commit()
        logs = await _get_docker_logs(bot.container_id)

    # Decrypt env vars for display
    env_vars_text = ""
    if bot.env_vars:
        try:
            env_vars_text = fernet.decrypt(bot.env_vars.encode()).decode()
        except Exception:
            env_vars_text = ""

    # Flash messages
    msg = request.query_params.get("msg", "")

    return templates.TemplateResponse(
        "bot_detail.html",
        {
            "request": request,
            "bot": bot,
            "logs": logs,
            "deploying": deploying,
            "env_vars_text": env_vars_text,
            "msg": msg,
        },
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

    # Parse custom env vars
    extra_env = {}
    if bot.env_vars:
        try:
            env_text = fernet.decrypt(bot.env_vars.encode()).decode()
            for line in env_text.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    extra_env[k.strip()] = v.strip()
        except Exception:
            pass

    bot.status = "deploying"
    await db.commit()

    start_deploy(bot.id, bot.name, bot.filename, token, extra_env)

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


@router.post("/{bot_id}/update-token")
async def update_token(
    request: Request,
    bot_id: int,
    telegram_token: str = Form(...),
    auto_redeploy: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot:
        return RedirectResponse("/", status_code=303)

    bot.telegram_token = fernet.encrypt(telegram_token.encode()).decode()
    await db.commit()

    if auto_redeploy == "on" and bot.container_id:
        return await _trigger_redeploy(bot, db)

    return RedirectResponse(f"/bots/{bot.id}?msg=token_updated", status_code=303)


@router.post("/{bot_id}/update-env")
async def update_env_vars(
    request: Request,
    bot_id: int,
    env_vars: str = Form(""),
    auto_redeploy: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot:
        return RedirectResponse("/", status_code=303)

    # Validate format: KEY=VALUE per line
    cleaned = []
    for line in env_vars.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            return RedirectResponse(f"/bots/{bot.id}?msg=invalid_env", status_code=303)
        cleaned.append(line)

    env_text = "\n".join(cleaned)
    bot.env_vars = fernet.encrypt(env_text.encode()).decode() if env_text else ""
    await db.commit()

    if auto_redeploy == "on" and bot.container_id:
        return await _trigger_redeploy(bot, db)

    return RedirectResponse(f"/bots/{bot.id}?msg=env_updated", status_code=303)


async def _trigger_redeploy(bot, db):
    """Stop current container and start a fresh deploy."""
    existing_job = get_job(bot.id)
    if existing_job and existing_job.phase not in (DeployPhase.DONE, DeployPhase.FAILED):
        return RedirectResponse(f"/bots/{bot.id}", status_code=303)

    token = fernet.decrypt(bot.telegram_token.encode()).decode()

    extra_env = {}
    if bot.env_vars:
        try:
            env_text = fernet.decrypt(bot.env_vars.encode()).decode()
            for line in env_text.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    extra_env[k.strip()] = v.strip()
        except Exception:
            pass

    bot.status = "deploying"
    await db.commit()

    start_deploy(bot.id, bot.name, bot.filename, token, extra_env)
    return RedirectResponse(f"/bots/{bot.id}", status_code=303)


@router.post("/{bot_id}/update-code")
async def update_code(
    request: Request,
    bot_id: int,
    bot_file: UploadFile = File(...),
    entrypoint: str = Form(""),
    requirements_file: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    bot = await db.get(Bot, bot_id)
    if not bot:
        return RedirectResponse("/", status_code=303)

    bot_dir = UPLOAD_DIR / bot.name

    # Clean old files (keep directory)
    if bot_dir.exists():
        for item in bot_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    else:
        bot_dir.mkdir(parents=True, exist_ok=True)

    original_filename = bot_file.filename or "bot.py"
    content = await bot_file.read()
    is_zip = original_filename.lower().endswith(".zip")

    if is_zip:
        zip_path = bot_dir / original_filename
        zip_path.write_bytes(content)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                extract_err = _safe_zip_extract(zf, bot_dir)
                if extract_err:
                    return RedirectResponse(
                        f"/bots/{bot.id}?msg=unsafe_zip", status_code=303
                    )
        except zipfile.BadZipFile:
            return RedirectResponse(f"/bots/{bot.id}?msg=bad_zip", status_code=303)
        zip_path.unlink()

        filename = _resolve_entrypoint(bot_dir, entrypoint)
        if not filename or not _validate_entrypoint(bot_dir, filename):
            return RedirectResponse(f"/bots/{bot.id}?msg=no_entrypoint", status_code=303)
    else:
        filename = original_filename
        (bot_dir / filename).write_bytes(content)

    # Save requirements.txt if provided
    if requirements_file and requirements_file.filename:
        req_content = await requirements_file.read()
        if req_content.strip():
            (bot_dir / "requirements.txt").write_bytes(req_content)

    bot.filename = filename
    await db.commit()

    return RedirectResponse(f"/bots/{bot.id}?msg=code_updated", status_code=303)


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
    elif bot.status == "deploying" and not job:
        # Stale: no active job but DB says deploying
        bot.status = "error"
        await db.commit()
    elif bot.container_id:
        docker_status = await _get_docker_status(bot.container_id)
        if docker_status == "running":
            bot.status = "running"
        elif docker_status == "exited":
            bot.status = "stopped"
        elif docker_status == "not_found":
            bot.status = "error"
        await db.commit()

    return templates.TemplateResponse("partials/bot_status.html", {"request": request, "bot": bot})
