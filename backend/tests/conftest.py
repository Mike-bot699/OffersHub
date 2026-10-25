import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

os.environ["EGI_ENV"] = "test"
os.environ["EGI_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["EGI_SCHEDULER_ENABLED"] = "false"
os.environ.pop("EGI_ANTHROPIC_API_KEY", None)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EGI_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    import app.models  # noqa: F401
    from app.core.config import get_settings
    from app.db import session as session_mod
    from app.db.base import Base

    get_settings.cache_clear()
    session_mod.reset_engine()
    Base.metadata.create_all(session_mod.get_engine())
    s = session_mod.SessionLocal()
    try:
        yield s
    finally:
        s.close()
        session_mod.reset_engine()
        get_settings.cache_clear()


@pytest.fixture()
def store(db):
    from app.models import Store

    s = Store(code="TST", name="Test Store", currency="USD", timezone="UTC")
    db.add(s)
    db.commit()
    return s
