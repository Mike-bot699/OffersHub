"""Approval workflow for consequential store changes.

Phase status: preparation, approval and audit are implemented. Automated execution
through platform write APIs is deliberately NOT enabled: it requires write scopes,
verification against a live store, and rollback testing that have not been done yet.
Until then an approved change is applied by the user in Shopify and marked
`applied manually`; the next sync then verifies whether the store matches the
proposal (e.g. the variant now has the approved price).
"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import CapabilityNotSupported, ConflictError, NotFoundError, ValidationError
from app.db.base import utcnow
from app.models import ActionProposal, Integration, Store, Variant
from app.models.enums import ActionStatus
from app.services.audit import audit

ACTION_KINDS = {
    "update_variant_price": {
        "label": "Change a variant's price",
        "required_scopes": ["write_products"],
        "automation": "not_enabled",
    },
    "create_discount_code": {
        "label": "Create a discount code",
        "required_scopes": ["write_discounts"],
        "automation": "not_enabled",
    },
    "create_automatic_gift": {
        "label": "Create an automatic free-gift / threshold discount",
        "required_scopes": ["write_discounts"],
        "automation": "not_enabled",
    },
}


def _idempotency_key(store_id: int, kind: str, payload: dict) -> str:
    raw = json.dumps({"s": store_id, "k": kind, "p": payload}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def preview(db: Session, store: Store, kind: str, payload: dict) -> dict:
    if kind not in ACTION_KINDS:
        raise ValidationError(f"Unknown action kind '{kind}'.")
    if kind == "update_variant_price":
        vid = payload.get("variant_id")
        new_price = payload.get("new_price")
        v = db.get(Variant, vid) if vid else None
        if v is None or v.store_id != store.id:
            raise ValidationError("Variant not found in this store.")
        if not isinstance(new_price, (int, float)) or new_price <= 0:
            raise ValidationError("new_price must be a positive number.")
        change = (new_price - v.price) / v.price if v.price else None
        if change is not None and abs(change) > 0.30:
            raise ValidationError("Price changes above ±30% are blocked as a safeguard; split into steps.")
        return {"current": {"price": v.price, "compare_at_price": v.compare_at_price},
                "proposed": {"price": new_price},
                "affected": {"variant_id": v.id, "sku": v.sku, "product_id": v.product_id,
                             "variant_source_id": v.source_id, "title": v.title},
                "rollback": {"price": v.price}}
    if kind in ("create_discount_code", "create_automatic_gift"):
        code = (payload.get("code") or "").strip().upper()
        if kind == "create_discount_code" and not code:
            raise ValidationError("A code is required.")
        return {"current": {"exists": False}, "proposed": payload, "affected": {"store": store.code},
                "rollback": {"deactivate": code or payload.get("title")}}
    raise ValidationError("Unsupported action.")


def create_proposal(db: Session, store: Store, kind: str, payload: dict, *, title: str,
                    expected_impact: dict | None = None, assumptions: list | None = None, risks: list | None = None,
                    recommendation_id: int | None = None, experiment_id: int | None = None) -> ActionProposal:
    if store.is_demo:
        raise ValidationError("Demo stores cannot have live store changes.")
    pv = preview(db, store, kind, payload)
    key = _idempotency_key(store.id, kind, payload)
    existing = db.scalar(select(ActionProposal).where(ActionProposal.idempotency_key == key))
    if existing and existing.status not in (ActionStatus.REJECTED, ActionStatus.FAILED, ActionStatus.ROLLED_BACK):
        raise ConflictError("An identical proposal already exists.", details={"proposal_id": existing.id})
    if existing:
        key = f"{key[:30]}-{int(utcnow().timestamp())}"
    prop = ActionProposal(
        store_id=store.id, kind=kind, title=title, payload=payload, current_values=pv["current"],
        proposed_values=pv["proposed"], affected=pv["affected"], expected_impact=expected_impact or {},
        assumptions=assumptions or [], risks=risks or [],
        required_permissions=ACTION_KINDS[kind]["required_scopes"], status=ActionStatus.PENDING_APPROVAL,
        idempotency_key=key, rollback_payload=pv["rollback"], recommendation_id=recommendation_id,
        experiment_id=experiment_id)
    db.add(prop)
    db.flush()
    audit(db, "action.proposed", "action_proposal", prop.id, store_id=store.id,
          details={"kind": kind, "current": pv["current"], "proposed": pv["proposed"]})
    return prop


def approve(db: Session, prop: ActionProposal, *, confirm_store_code: str, actor: str = "owner") -> ActionProposal:
    store = db.get(Store, prop.store_id)
    if prop.status != ActionStatus.PENDING_APPROVAL:
        raise ConflictError(f"Proposal is '{prop.status}', not pending approval.")
    if confirm_store_code.strip().upper() != store.code.upper():
        raise ValidationError("Type the store code exactly to confirm the approval.")
    # Re-check that current values have not changed since the proposal was made
    fresh = preview(db, store, prop.kind, prop.payload)
    if fresh["current"] != prop.current_values:
        raise ConflictError("The store's current values changed since this proposal was created. Re-create it.",
                            details={"was": prop.current_values, "now": fresh["current"]})
    prop.status = ActionStatus.APPROVED
    prop.approved_by = actor
    prop.approved_at = utcnow()
    audit(db, "action.approved", "action_proposal", prop.id, store_id=prop.store_id, actor=actor)
    return prop


def reject(db: Session, prop: ActionProposal, reason: str, actor: str = "owner") -> ActionProposal:
    if prop.status not in (ActionStatus.PENDING_APPROVAL, ActionStatus.APPROVED):
        raise ConflictError(f"Proposal is '{prop.status}'.")
    prop.status = ActionStatus.REJECTED
    prop.error = reason
    audit(db, "action.rejected", "action_proposal", prop.id, store_id=prop.store_id, actor=actor,
          details={"reason": reason})
    return prop


def execute(db: Session, prop: ActionProposal) -> ActionProposal:
    """Automated execution is not enabled in this phase; the attempt is recorded."""
    if prop.status != ActionStatus.APPROVED:
        raise ConflictError("Only approved proposals can be executed.")
    integ = db.scalar(select(Integration).where(Integration.store_id == prop.store_id))
    missing = [s for s in prop.required_permissions if s not in (integ.granted_scopes if integ else [])]
    audit(db, "action.execute_blocked", "action_proposal", prop.id, store_id=prop.store_id,
          details={"missing_scopes": missing, "reason": "automation_not_enabled"})
    raise CapabilityNotSupported(
        "Automated execution is not enabled yet. Apply the change in Shopify admin, then mark it as applied "
        "manually; the next sync will verify it." + (f" (Would also need scopes: {', '.join(missing)}.)"
                                                     if missing else ""))


def mark_applied_manually(db: Session, prop: ActionProposal, actor: str = "owner") -> ActionProposal:
    if prop.status != ActionStatus.APPROVED:
        raise ConflictError("Only approved proposals can be marked as applied.")
    prop.status = ActionStatus.SUCCEEDED
    prop.executed_at = utcnow()
    prop.result = {"mode": "manual", "verification": "pending_next_sync"}
    audit(db, "action.applied_manually", "action_proposal", prop.id, store_id=prop.store_id, actor=actor)
    return prop


def verify_applied(db: Session, prop: ActionProposal) -> dict:
    """Check the synced store state against an applied proposal."""
    if prop.kind == "update_variant_price":
        v = db.get(Variant, prop.affected.get("variant_id"))
        if v is None:
            raise NotFoundError("Variant no longer exists.")
        ok = v.price is not None and abs(v.price - prop.proposed_values["price"]) < 0.005
        prop.result = {**(prop.result or {}), "verification": "matched" if ok else "not_matched",
                       "observed_price": v.price, "verified_at": utcnow().isoformat()}
        return prop.result
    prop.result = {**(prop.result or {}), "verification": "not_supported_for_kind"}
    return prop.result
