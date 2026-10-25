from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    CostRecord,
    Coupon,
    Customer,
    Order,
    OrderLine,
    PriceHistory,
    Product,
    SyncState,
    Variant,
)


def purge_platform_data(db: Session, store_id: int, *, source: str) -> dict:
    """Delete everything a given source contributed to one store. Never touches other stores
    or other sources (e.g. imported costs or offer history stay)."""
    order_ids = select(Order.id).where(Order.store_id == store_id, Order.source == source)
    counts = {
        "order_lines": db.execute(delete(OrderLine).where(OrderLine.order_id.in_(order_ids))).rowcount,
        "orders": db.execute(delete(Order).where(Order.store_id == store_id, Order.source == source)).rowcount,
        "customers": db.execute(delete(Customer).where(Customer.store_id == store_id,
                                                       Customer.source == source)).rowcount,
        "coupons": db.execute(delete(Coupon).where(Coupon.store_id == store_id, Coupon.source == source)).rowcount,
    }
    variant_ids = select(Variant.id).where(Variant.store_id == store_id, Variant.source == source)
    counts["price_history"] = db.execute(delete(PriceHistory).where(PriceHistory.variant_id.in_(variant_ids))).rowcount
    counts["cost_records"] = db.execute(delete(CostRecord).where(CostRecord.store_id == store_id,
                                                                 CostRecord.source == source)).rowcount
    counts["variants"] = db.execute(delete(Variant).where(Variant.store_id == store_id,
                                                          Variant.source == source)).rowcount
    counts["products"] = db.execute(delete(Product).where(Product.store_id == store_id,
                                                          Product.source == source)).rowcount
    db.execute(delete(SyncState).where(SyncState.store_id == store_id))
    return counts
