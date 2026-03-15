from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.database import init_db
from app.routes import auth, dashboard, bots, logs, generator


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Check Docker connection
    from app.docker_manager import check_docker_connection
    ok, msg = check_docker_connection()
    if ok:
        print(f"[startup] Docker connected: {msg}")
    else:
        print(f"[startup] WARNING: Docker not available: {msg}")
        print("[startup] Bot deployment will fail. Mount /var/run/docker.sock if running in Docker.")

    yield


app = FastAPI(title="Telegram Bot Deployer", lifespan=lifespan)

from app.middleware import SecurityHeadersMiddleware
app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(bots.router)
app.include_router(logs.router)
app.include_router(generator.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["uploads/*", "*.db"],
    )
