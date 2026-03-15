from datetime import datetime, timezone
from sqlalchemy import Table, Column, Integer, String, Text, DateTime
from app.database import Base


class Bot(Base):
    __tablename__ = "bots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    telegram_token = Column(Text, nullable=False)  # encrypted
    filename = Column(String(255), nullable=False)
    container_id = Column(String(100), nullable=True)
    env_vars = Column(Text, nullable=True, default="")  # KEY=VALUE per line, encrypted
    status = Column(String(20), default="created")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
