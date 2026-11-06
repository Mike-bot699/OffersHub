"""Copy every table from another database (e.g. Neon) into this deployment's database.

Used to move OffersHub between hosts. Read-only on the source. The target must be migrated and
empty; both must be at the same schema revision. After copying, the row count of every table is
compared and any difference is reported (the copy is then not to be trusted)."""

from sqlalchemy import create_engine, func, inspect, select, text

import app.models  # noqa: F401  (register every table)
from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.db.base import Base
from app.db.session import get_engine

BATCH = 1000


def _revision(conn) -> str | None:
    if not inspect(conn).has_table("alembic_version"):
        return None
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def copy_from(source_url: str) -> dict:
    src = create_engine(Settings(database_url=source_url).database_url, future=True)
    dst = get_engine()
    tables = Base.metadata.sorted_tables  # parents before children
    with src.connect() as s, dst.begin() as d:
        rs, rd = _revision(s), _revision(d)
        if rs is None or rs != rd:
            raise ConfigurationError(f"Schema revisions differ (source {rs}, target {rd}): migrate both first.")
        missing = [t.name for t in tables if not inspect(s).has_table(t.name)]
        if missing:
            raise ConfigurationError(f"Source is missing tables: {', '.join(missing)}")
        busy = [t.name for t in tables if d.execute(select(func.count()).select_from(t)).scalar()]
        if busy:
            raise ConfigurationError(f"Target is not empty ({', '.join(busy[:5])}…): refusing to mix data.")
        for t in tables:
            rows = s.execute(select(t)).mappings()
            while chunk := rows.fetchmany(BATCH):
                d.execute(t.insert(), [dict(r) for r in chunk])
        if dst.dialect.name == "postgresql":  # keep new ids after the copied ones
            for t in tables:
                for c in t.primary_key.columns:
                    if c.autoincrement is not False and c.type.python_type is int:
                        d.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', '{c.name}'), "
                                       f"COALESCE((SELECT MAX({c.name}) FROM {t.name}), 0) + 1, false)"))
    counts, diff = {}, {}
    with src.connect() as s, dst.connect() as d:
        for t in tables:
            a = s.execute(select(func.count()).select_from(t)).scalar()
            b = d.execute(select(func.count()).select_from(t)).scalar()
            counts[t.name] = b
            if a != b:
                diff[t.name] = {"source": a, "target": b}
    src.dispose()
    return {"ok": not diff, "tables": len(tables), "rows": sum(counts.values()), "differences": diff, "counts": counts}
