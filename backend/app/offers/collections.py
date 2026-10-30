"""Store collections from the platform, used to name and size an offer's Level targets.

Matching a target to a collection: by handle, then by title, case-insensitive. A target that
matches more than one collection is reported as ambiguous, never resolved by guessing.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Collection, Store


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def store_collections(db: Session, store: Store, collections: list[dict], memberships: list[dict], *, complete: bool) -> dict:
    """Replace the store's collections (rows with snake_case keys)."""
    members: dict[str, list[str]] = {}
    for m in memberships:
        cid, pid = m.get("collection_id"), m.get("product_id")
        if cid not in (None, "") and pid not in (None, ""):
            members.setdefault(str(cid), []).append(str(pid))
    now = utcnow()
    existing = {c.source_id: c for c in db.scalars(select(Collection).where(Collection.store_id == store.id))}
    seen = 0
    for r in collections:
        cid = str(r.get("id") or r.get("collection_id") or "").strip()
        title = str(r.get("title") or "").strip()
        if not cid or not title:
            continue
        c = existing.pop(cid, None) or Collection(store_id=store.id, source_id=cid)
        if c.id is None:
            db.add(c)
        c.title, c.handle = title[:300], (str(r.get("handle") or "").strip().lower() or None)
        c.kind = (str(r.get("kind") or "").lower() or None)
        c.published = r.get("published") if isinstance(r.get("published"), bool) else None
        pc = r.get("product_count")
        c.product_count = int(pc) if isinstance(pc, (int, float)) or str(pc or "").isdigit() else None
        c.rules = r.get("rules") if isinstance(r.get("rules"), list) else []
        c.disjunctive = r.get("disjunctive") if isinstance(r.get("disjunctive"), bool) else None
        c.product_ids = sorted(set(members.get(cid, [])))
        c.synced_at = now
        seen += 1
    removed = 0
    if complete:
        for c in existing.values():
            db.delete(c)
            removed += 1
    db.flush()
    return {"collections": seen, "memberships": len(memberships), "removed": removed, "complete": complete}


def collection_index(db: Session, store_id: int) -> dict[str, list[Collection]]:
    """Normalised handle and title -> collections."""
    idx: dict[str, list[Collection]] = {}
    for c in db.scalars(select(Collection).where(Collection.store_id == store_id)):
        for k in {_norm(c.handle), _norm(c.title)} - {""}:
            idx.setdefault(k, []).append(c)
    return idx


def resolve_targets(targets: list[str], idx: dict[str, list[Collection]]) -> list[dict]:
    out = []
    for t in targets:
        hits = {c.id: c for c in idx.get(_norm(t), [])}
        if len(hits) == 1:
            c = next(iter(hits.values()))
            out.append({"target": t, "status": "matched", "title": c.title, "handle": c.handle, "kind": c.kind,
                        "products": c.product_count if c.product_count is not None else len(c.product_ids or []),
                        "published": c.published})
        else:
            out.append({"target": t, "status": "ambiguous" if hits else "not_found"})
    return out


def attach_levels(db: Session, rows: list[dict]) -> None:
    """Add `level_resolved` to serialised collection-level offers (per store, one index each)."""
    idx: dict[int, dict] = {}
    for r in rows:
        if r.get("level") != "collection" or not r.get("level_targets"):
            r["level_resolved"] = None
            continue
        if r["store_id"] not in idx:
            idx[r["store_id"]] = collection_index(db, r["store_id"])
        r["level_resolved"] = resolve_targets(r["level_targets"], idx[r["store_id"]]) if idx[r["store_id"]] else None
