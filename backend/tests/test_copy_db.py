"""Moving hosts: a full copy from another database, verified row by row count."""

from datetime import date

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.errors import ConfigurationError
from app.db.base import utcnow

ALEMBIC = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "alembic.ini")


def _migrated(url, monkeypatch):
    from app.core.config import get_settings
    from app.db import session

    monkeypatch.setenv("EGI_DATABASE_URL", url)
    get_settings.cache_clear()
    session.reset_engine()
    command.upgrade(Config(ALEMBIC), "head")


def test_copy_all_tables_and_refuse_a_non_empty_target(tmp_path, monkeypatch):
    from app.db import session
    from app.models import OfferDoc, PromoEvent, Store
    from app.services.copy_db import copy_from

    src = f"sqlite:///{tmp_path / 'src.db'}"
    _migrated(src, monkeypatch)
    with session.session_scope() as db:
        s = Store(code="MAU", name="Little Learners Toys", currency="USD", timezone="Europe/London")
        db.add(s)
        db.flush()
        db.add_all([PromoEvent(store_id=s.id, name="Tricks, Treats & Clever Feats", source="manual", status="live",
                               start_date=date(2026, 10, 25), end_date=date(2026, 11, 1), coupon_codes=["SPOOKLAB"]),
                    OfferDoc(token="t" * 32, name="MAU · Halloween 2026", tags=["Halloween"], store_ids=[s.id],
                             created_at=utcnow(), updated_at=utcnow())])
    _migrated(f"sqlite:///{tmp_path / 'dst.db'}", monkeypatch)
    out = copy_from(src)
    assert out["ok"] and out["differences"] == {}
    assert out["counts"]["stores"] == 1 and out["counts"]["promo_events"] == 1 and out["counts"]["offer_docs"] == 1
    with session.session_scope() as db:
        ev = db.query(PromoEvent).one()
        assert ev.coupon_codes == ["SPOOKLAB"] and ev.end_date == date(2026, 11, 1)  # JSON and dates intact
    with pytest.raises(ConfigurationError, match="not empty"):
        copy_from(src)  # a second run would duplicate: refused


def test_refuses_a_source_at_another_schema_revision(tmp_path, monkeypatch):
    from app.services.copy_db import copy_from

    src = f"sqlite:///{tmp_path / 'src.db'}"
    _migrated(src, monkeypatch)
    with create_engine(src).begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = 'older'"))
    _migrated(f"sqlite:///{tmp_path / 'dst.db'}", monkeypatch)
    with pytest.raises(ConfigurationError, match="revisions differ"):
        copy_from(src)
