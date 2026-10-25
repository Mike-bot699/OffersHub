"""GraphQL documents for the Shopify Admin API (target version: see settings.shopify_api_version).

Field selections are shared between bulk operations and the small "probe" queries run on
connect, so a schema change in a new API version surfaces as a clear error at connection
time instead of a failed overnight sync.
"""

SHOP = """
query Shop {
  shop { name myshopifyDomain currencyCode ianaTimezone }
  currentAppInstallation { accessScopes { handle } }
}
"""

MONEY = "shopMoney { amount }"

VARIANT_FIELDS = """
  __typename id title sku price compareAtPrice inventoryQuantity availableForSale updatedAt
"""
VARIANT_COST_FIELDS = "inventoryItem { id tracked unitCost { amount currencyCode } }"

PRODUCT_FIELDS = "__typename id title handle productType vendor status tags createdAt updatedAt"

ORDER_FIELDS = f"""
  __typename id name processedAt createdAt updatedAt cancelledAt test currencyCode
  displayFinancialStatus displayFulfillmentStatus sourceName discountCodes
  subtotalPriceSet {{ {MONEY} }}
  totalDiscountsSet {{ {MONEY} }}
  totalShippingPriceSet {{ {MONEY} }}
  totalTaxSet {{ {MONEY} }}
  totalPriceSet {{ {MONEY} }}
  totalRefundedSet {{ {MONEY} }}
"""
ORDER_CUSTOMER_FIELDS = "customer { id }"

DISCOUNT_APPLICATION_FIELDS = """
  __typename allocationMethod targetSelection targetType
  value { __typename ... on MoneyV2 { amount } ... on PricingPercentageValue { percentage } }
  ... on DiscountCodeApplication { code }
  ... on AutomaticDiscountApplication { title }
  ... on ManualDiscountApplication { title }
"""

LINE_ITEM_FIELDS = f"""
  __typename id title variantTitle sku quantity isGiftCard
  originalUnitPriceSet {{ {MONEY} }}
  discountAllocations {{ allocatedAmountSet {{ {MONEY} }} }}
  product {{ id }}
  variant {{ id }}
"""


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def bulk_products_query(search: str, with_costs: bool) -> str:
    cost = VARIANT_COST_FIELDS if with_costs else ""
    return f"""
{{
  products(query: "{_esc(search)}") {{
    edges {{ node {{
      {PRODUCT_FIELDS}
      variants {{ edges {{ node {{ {VARIANT_FIELDS} {cost} }} }} }}
    }} }}
  }}
}}
"""


def bulk_orders_query(search: str, with_customer: bool) -> str:
    cust = ORDER_CUSTOMER_FIELDS if with_customer else ""
    return f"""
{{
  orders(query: "{_esc(search)}") {{
    edges {{ node {{
      {ORDER_FIELDS}
      {cust}
      discountApplications {{ edges {{ node {{ {DISCOUNT_APPLICATION_FIELDS} }} }} }}
      lineItems {{ edges {{ node {{ {LINE_ITEM_FIELDS} }} }} }}
    }} }}
  }}
}}
"""


def probe_products_query(with_costs: bool) -> str:
    cost = VARIANT_COST_FIELDS if with_costs else ""
    return f"""
query Probe {{
  products(first: 1) {{ nodes {{ {PRODUCT_FIELDS} variants(first: 1) {{ nodes {{ {VARIANT_FIELDS} {cost} }} }} }} }}
}}
"""


def probe_orders_query(with_customer: bool) -> str:
    cust = ORDER_CUSTOMER_FIELDS if with_customer else ""
    return f"""
query Probe {{
  orders(first: 1) {{ nodes {{
    {ORDER_FIELDS} {cust}
    discountApplications(first: 1) {{ nodes {{ {DISCOUNT_APPLICATION_FIELDS} }} }}
    lineItems(first: 1) {{ nodes {{ {LINE_ITEM_FIELDS} }} }}
  }} }}
}}
"""


BULK_RUN = """
mutation RunBulk($query: String!) {
  bulkOperationRunQuery(query: $query) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
"""

BULK_STATUS = """
query BulkStatus($id: ID!) {
  node(id: $id) {
    ... on BulkOperation { id status errorCode objectCount url partialDataUrl }
  }
}
"""

REFUND_LINES = f"""
query RefundLines($ids: [ID!]!) {{
  nodes(ids: $ids) {{
    ... on Order {{
      id
      refunds {{
        refundLineItems(first: 100) {{
          nodes {{ quantity lineItem {{ id }} subtotalSet {{ {MONEY} }} }}
        }}
      }}
    }}
  }}
}}
"""

_CODE_DISCOUNT_COMMON = "title status startsAt endsAt asyncUsageCount codes(first: 5) { nodes { code } }"
_MIN_REQ = """
  minimumRequirement {
    __typename
    ... on DiscountMinimumSubtotal { greaterThanOrEqualToSubtotal { amount } }
    ... on DiscountMinimumQuantity { greaterThanOrEqualToQuantity }
  }
"""
_GETS_VALUE = """
  customerGets { value {
    __typename
    ... on DiscountPercentage { percentage }
    ... on DiscountAmount { amount { amount } }
  } }
"""

CODE_DISCOUNTS = f"""
query CodeDiscounts($cursor: String) {{
  codeDiscountNodes(first: 25, after: $cursor) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      id
      codeDiscount {{
        __typename
        ... on DiscountCodeBasic {{ {_CODE_DISCOUNT_COMMON} usageLimit appliesOncePerCustomer {_MIN_REQ} {_GETS_VALUE} }}
        ... on DiscountCodeBxgy {{ {_CODE_DISCOUNT_COMMON} usageLimit appliesOncePerCustomer }}
        ... on DiscountCodeFreeShipping {{ {_CODE_DISCOUNT_COMMON} usageLimit appliesOncePerCustomer {_MIN_REQ} }}
        ... on DiscountCodeApp {{ {_CODE_DISCOUNT_COMMON} usageLimit appliesOncePerCustomer }}
      }}
    }}
  }}
}}
"""

AUTOMATIC_DISCOUNTS = f"""
query AutomaticDiscounts($cursor: String) {{
  automaticDiscountNodes(first: 25, after: $cursor) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      id
      automaticDiscount {{
        __typename
        ... on DiscountAutomaticBasic {{ title status startsAt endsAt asyncUsageCount {_MIN_REQ} {_GETS_VALUE} }}
        ... on DiscountAutomaticBxgy {{ title status startsAt endsAt asyncUsageCount }}
        ... on DiscountAutomaticFreeShipping {{ title status startsAt endsAt asyncUsageCount {_MIN_REQ} }}
        ... on DiscountAutomaticApp {{ title status startsAt endsAt asyncUsageCount }}
      }}
    }}
  }}
}}
"""
