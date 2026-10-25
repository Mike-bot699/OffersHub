"""Backups of persistent data.

SQLite: an online, consistent copy using SQLite's backup API (safe while the app runs).
PostgreSQL: use pg_dump (see docs/OPERATIONS.md); this module only handles SQLite.
The encryption key (EGI_SECRET_KEY) is NOT in the backup — store it separately, or
restored integrations will need to be reconnected.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import ConfigurationError


def _db_path() -> Path:
    s = get_settings()
    if not s.is_sqlite:
        raise ConfigurationError("Built-in backup supports SQLite only; use pg_dump for PostgreSQL.")
    return Path(s.database_url.removeprefix("sqlite:///"))


def backup_dir() -> Path:
    d = _db_path().parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_backup() -> dict:
    src = _db_path()
    if not src.exists():
        raise ConfigurationError("Database file not found.")
    dest = backup_dir() / f"egi-{datetime.now(UTC):%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dest) as d:
        s.backup(d)
    with sqlite3.connect(dest) as d:
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        dest.unlink(missing_ok=True)
        raise ConfigurationError(f"Backup failed integrity check: {ok}")
    return {"file": dest.name, "bytes": dest.stat().st_size, "integrity": ok}


def list_backups() -> list[dict]:
    return [{"file": p.name, "bytes": p.stat().st_size,
             "created": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat()}
            for p in sorted(backup_dir().glob("egi-*.db"), reverse=True)]


def restore_backup(name: str) -> dict:
    """Replace the live database with a backup. Takes a safety backup of the current state first.
    Run only while the application is stopped (CLI command)."""
    src = backup_dir() / name
    if not src.exists() or src.parent != backup_dir():
        raise ConfigurationError("Backup not found.")
    safety = create_backup()
    with sqlite3.connect(src) as s, sqlite3.connect(_db_path()) as d:
        s.backup(d)
    return {"restored": name, "safety_backup": safety["file"]}
