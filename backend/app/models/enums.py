"""Controlled vocabularies. Stored as strings so migrations stay portable."""

from enum import StrEnum


class DataSource(StrEnum):
    SHOPIFY = "shopify"                     # pulled from a connected Shopify store
    SPREADSHEET_IMPORT = "spreadsheet_import"  # historical offers-monitoring workbook
    CSV_IMPORT = "csv_import"               # user-supplied CSV (costs, traffic, ...)
    MANUAL = "manual"                       # typed into this application
    ASSUMPTION = "assumption"               # user-declared assumption (e.g. default COGS %)
    DEMO = "demo"                           # synthetic demo data, never mixed with real stores
    DERIVED = "derived"                     # computed by this application


class Objective(StrEnum):
    MAXIMIZE_CONTRIBUTION = "maximize_contribution_margin"
    GROW_REVENUE_WITH_MARGIN_FLOOR = "grow_revenue_with_margin_floor"
    INCREASE_AOV = "increase_aov"
    IMPROVE_CONVERSION = "improve_conversion"
    INCREASE_REPEAT = "increase_repeat_purchases"
    REDUCE_DISCOUNT_RELIANCE = "reduce_discount_reliance"
    GROW_SELECTED_PRODUCTS = "grow_selected_products"
    DIVERSIFY_REVENUE = "diversify_revenue"


class StrategyType(StrEnum):
    PRICING = "pricing"
    PRODUCT_EXPANSION = "product_expansion"
    BUNDLE = "bundle"
    FREE_GIFT = "free_gift"
    QUANTITY_INCENTIVE = "quantity_incentive"
    SPEND_THRESHOLD = "spend_threshold"
    FREE_SHIPPING = "free_shipping"
    GAMIFICATION = "cart_gamification"
    CROSS_SELL = "cross_sell_upsell"
    CONVERSION = "conversion"
    RETENTION = "retention"
    MERCHANDISING = "merchandising"
    PROMOTION = "promotion"
    DISCOUNT_REDUCTION = "discount_reduction"
    DIVERSIFICATION = "diversification"
    SEASONAL = "seasonal"
    SEGMENTATION = "segmentation"
    INVENTORY = "inventory"
    DATA_QUALITY = "data_quality"


class OfferType(StrEnum):
    PERCENTAGE = "percentage_discount"
    FIXED = "fixed_discount"
    TIERED = "tiered_discount"
    BXGY = "buy_x_get_y"
    FREE_GIFT = "free_gift"
    BUNDLE = "bundle"
    FREE_SHIPPING = "free_shipping"
    SPEND_THRESHOLD = "spend_threshold"
    QUANTITY = "quantity_incentive"
    SECOND_ITEM = "second_item"
    NEW_CUSTOMER = "new_customer"
    RETURNING_CUSTOMER = "returning_customer"
    PRODUCT_SPECIFIC = "product_specific"
    SPECIAL_PRICE = "special_price"
    VOUCHER = "future_voucher"      # store credit / voucher for a future purchase
    LOYALTY = "loyalty_points"
    MESSAGE_ONLY = "message_only"   # campaign with no commercial incentive
    OTHER = "other"


class IntegrationStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"     # credentials stored, connection not yet verified
    CONNECTED = "connected"       # verified against the live API
    ERROR = "error"
    DISCONNECTED = "disconnected"


class SyncStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ExperimentStatus(StrEnum):
    OPPORTUNITY = "opportunity"
    HYPOTHESIS = "hypothesis"
    SIMULATED = "simulated"
    APPROVED = "approved"
    IMPLEMENTING = "implementing"
    MEASURING = "measuring"
    EVALUATED = "evaluated"
    CLOSED = "closed"
    ABANDONED = "abandoned"


EXPERIMENT_FLOW = [
    ExperimentStatus.OPPORTUNITY,
    ExperimentStatus.HYPOTHESIS,
    ExperimentStatus.SIMULATED,
    ExperimentStatus.APPROVED,
    ExperimentStatus.IMPLEMENTING,
    ExperimentStatus.MEASURING,
    ExperimentStatus.EVALUATED,
    ExperimentStatus.CLOSED,
]


class EvidenceStrength(StrEnum):
    STRONG = "strong"            # controlled comparison, adequate sample, significant
    MODERATE = "moderate"        # controlled but under-powered, or quasi-experimental with controls
    WEAK = "weak"                # before/after or uncontrolled observation
    INCONCLUSIVE = "inconclusive"


class ActionStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class OfferStatus(StrEnum):
    """Operational stage of an offer. Set by people (or by store sync for store-detected offers),
    never inferred from dates alone. NULL in the database means "not set — needs review"."""

    IN_PROGRESS = "in_progress"   # being prepared, configured or implemented; not live
    SCHEDULED = "scheduled"       # ready, planned to launch at a future date
    LIVE = "live"                 # launched and running
    PAUSED = "paused"             # temporarily suspended
    COMPLETED = "completed"       # ended as planned
    CANCELLED = "cancelled"       # will not launch / was cancelled


OFFER_STATUSES = [s.value for s in OfferStatus]


class TechnicalStatus(StrEnum):
    """What the connected store says about the offer's discount, from API evidence only."""

    ACTIVE = "active"             # discount found and active in the store
    INACTIVE = "inactive"         # found, but expired or not yet started
    UNAVAILABLE = "unavailable"   # store synced, but the code/discount is not there (or was deleted)
    UNKNOWN = "unknown"           # no store evidence: not connected, not synced, or nothing to match


class VerificationStatus(StrEnum):
    DOCUMENTED_ONLY = "documented_only"      # exists in this app / spreadsheet only
    VERIFIED = "verified_in_store"           # matching configuration found in the connected store
    MISMATCH = "mismatch"                    # found, but configuration differs
    NOT_FOUND = "not_found_in_store"         # store connected and synced, code absent
    UNVERIFIABLE = "unverifiable"            # no connected source to check against
