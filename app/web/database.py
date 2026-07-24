import threading

from app.config import get_settings
from app.db import get_session_factory
from app.models import Base


_schema_lock = threading.Lock()
_schema_ready = False


def session_factory():
    global _schema_ready
    settings = get_settings()
    Session = get_session_factory(settings)
    if not _schema_ready:
        with _schema_lock:
            if not _schema_ready:
                Base.metadata.create_all(Session.kw["bind"])
                _schema_ready = True
    return Session
