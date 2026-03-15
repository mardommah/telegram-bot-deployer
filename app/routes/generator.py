from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.auth import get_current_user
from app.database import get_db
from app.models import Bot
from app.config import fernet, UPLOAD_DIR
from app.bot_templates import TEMPLATES, get_template

router = APIRouter(prefix="/generator")
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def template_list(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    # Group templates by category
    categories = {}
    for t in TEMPLATES.values():
        categories.setdefault(t.category, []).append(t)

    return templates.TemplateResponse(
        "generator_select.html",
        {"request": request, "categories": categories, "user": user},
    )


@router.get("/{template_id}", response_class=HTMLResponse)
async def template_config(request: Request, template_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    tmpl = get_template(template_id)
    if not tmpl:
        return RedirectResponse("/generator", status_code=303)

    return templates.TemplateResponse(
        "generator_config.html",
        {"request": request, "tmpl": tmpl, "error": None, "user": user},
    )


@router.post("/{template_id}")
async def generate_bot(
    request: Request,
    template_id: str,
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    tmpl = get_template(template_id)
    if not tmpl:
        return RedirectResponse("/generator", status_code=303)

    form = await request.form()
    bot_name = form.get("bot_name", "").strip()
    telegram_token = form.get("telegram_token", "").strip()

    if not bot_name or not telegram_token:
        return templates.TemplateResponse(
            "generator_config.html",
            {"request": request, "tmpl": tmpl, "error": "Bot name and token are required", "user": user},
        )

    # Check unique name
    existing = await db.execute(select(Bot).where(Bot.name == bot_name))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            "generator_config.html",
            {"request": request, "tmpl": tmpl, "error": f"Bot '{bot_name}' already exists", "user": user},
        )

    # Collect template field values
    field_values = {}
    for field in tmpl.fields:
        val = form.get(field.name, field.default or "").strip()
        if field.required and not val:
            return templates.TemplateResponse(
                "generator_config.html",
                {"request": request, "tmpl": tmpl, "error": f"'{field.label}' is required", "user": user},
            )
        field_values[field.name] = val

    # Generate bot code
    try:
        bot_code = tmpl.code_template.format(**field_values)
    except KeyError as e:
        return templates.TemplateResponse(
            "generator_config.html",
            {"request": request, "tmpl": tmpl, "error": f"Template error: missing field {e}", "user": user},
        )

    # Write files
    bot_dir = UPLOAD_DIR / bot_name
    bot_dir.mkdir(parents=True, exist_ok=True)

    (bot_dir / "bot.py").write_text(bot_code)

    if tmpl.requirements.strip():
        (bot_dir / "requirements.txt").write_text(tmpl.requirements.strip() + "\n")

    # Save to DB
    encrypted_token = fernet.encrypt(telegram_token.encode()).decode()
    bot = Bot(
        name=bot_name,
        telegram_token=encrypted_token,
        filename="bot.py",
        status="created",
    )
    db.add(bot)
    await db.commit()
    await db.refresh(bot)

    return RedirectResponse(f"/bots/{bot.id}", status_code=303)
