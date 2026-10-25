from datetime import date, datetime, timedelta
from typing import Any

from fastapi import Depends, Query
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.db.session import get_db
from app.models import Store

# Columns that must never leave the backend.
SECRET_COLUMNS = {"credentials_encrypted", "token_cache_encrypted"}


def to_dict(obj: Any, *, exclude: set[str] | None = None) -> dict:
    if obj is None:
        return None  # type: ignore[return-value]
    out = {}
    skip = SECRET_COLUMNS | (exclude or set())
    for col in sa_inspect(obj).mapper.column_attrs:
        if col.key in skip:
            continue
        v = getattr(obj, col.key)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[col.key] = v
    return out


def get_store(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if store is None:
        raise NotFoundError(f"Store {store_id} not found.")
    return store


def period(start: date | None = Query(None), end: date | None = Query(None),
           days: int = Query(90, ge=1, le=1095)) -> tuple[date, date]:
    end = end or date.today()
    start = start or end - timedelta(days=days - 1)
    if start > end:
        raise ValidationError("start must be on or before end.")
    if (end - start).days > 1095:
        raise ValidationError("Periods longer than 3 years are not supported.")
    return start, end


DB = Depends(get_db)
Period = Depends(period)
