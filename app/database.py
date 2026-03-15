from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    from app.migrations import run_migrations
    import app.models  # noqa: F401 — ensure models are registered in metadata

    async with engine.begin() as conn:
        # Migrate existing tables (add missing columns)
        await conn.run_sync(run_migrations)
        # Create any new tables
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with async_session() as session:
        yield session
