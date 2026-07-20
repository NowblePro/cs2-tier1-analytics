from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings


def get_engine(settings: Settings | None = None):
    settings = settings or get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, future=True, connect_args=connect_args)


def get_session_factory(settings: Settings | None = None):
    return sessionmaker(bind=get_engine(settings), autoflush=False, expire_on_commit=False, future=True)

