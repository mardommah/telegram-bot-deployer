import time
import hashlib
from collections import defaultdict
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from app.config import ADMIN_USERNAME, ADMIN_PASSWORD
from app.auth import create_session_token, SESSION_COOKIE, get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Rate limiting: track failed attempts per IP
_login_attempts: dict[str, list[float]] = defaultdict(list)
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 300  # 5 minutes


def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    # Clean old entries
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _WINDOW_SECONDS]
    return len(_login_attempts[ip]) >= _MAX_ATTEMPTS


def _record_failed_attempt(ip: str):
    _login_attempts[ip].append(time.time())


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"

    if _is_rate_limited(client_ip):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Too many login attempts. Try again in 5 minutes."},
            status_code=429,
        )

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        # Clear failed attempts on success
        _login_attempts.pop(client_ip, None)
        response = RedirectResponse("/", status_code=303)
        token = create_session_token(username)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            max_age=86400,
        )
        return response

    _record_failed_attempt(client_ip)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Invalid credentials"}, status_code=401
    )


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
