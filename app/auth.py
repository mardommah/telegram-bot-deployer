import time
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer
from app.config import SECRET_KEY

serializer = URLSafeTimedSerializer(SECRET_KEY)
SESSION_COOKIE = "session"
MAX_AGE = 86400  # 24 hours


def create_session_token(username: str) -> str:
    # Include timestamp to prevent session fixation
    return serializer.dumps({"user": username, "iat": int(time.time())})


def get_current_user(request: Request) -> str | None:  # noqa
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=MAX_AGE)
        return data.get("user")
    except Exception:
        return None


def require_admin(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user
