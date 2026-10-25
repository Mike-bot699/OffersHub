# Connecting Shopify

The connector uses the **Shopify Admin GraphQL API** (version set by `EGI_SHOPIFY_API_VERSION`,
default `2026-10`, stable since 1 October 2026) and is **read-only**.

> Verification note: shopify.dev was not reachable from the environment where this was built.
> Auth flows and query fields follow Shopify's documented 2025–2026 API (client credentials grant,
> bulk operations, discount nodes) and were tested against an emulated API. The connection screen runs
> a *probe* of every sync query on connect, so any field or permission that differs in your shop's API
> version is reported immediately with Shopify's own error message.

## Option A — Dev Dashboard app (recommended for new setups)

Stores created after 1 January 2026 can no longer create legacy custom apps in the admin.

1. In the Shopify **Dev Dashboard**, create an app in the **same organisation** as the store(s).
2. Configure Admin API access scopes (below) and install the app on each store.
3. Copy the app's **Client ID** and **Client secret**.
4. In the application: Integrations → choose the store → *Dev Dashboard app* → enter the
   `*.myshopify.com` domain, client ID and secret → *Connect & verify*.

The backend exchanges the credentials for a 24-hour token (`POST /admin/oauth/access_token`,
`grant_type=client_credentials`), caches it encrypted, and refreshes it automatically. This grant
only works when app and store belong to the same organisation (`shop_not_permitted` otherwise).

## Option B — Legacy custom app (existing admin-created apps)

Use the app's Admin API access token (`shpat_…`). Choose *Legacy custom app* and paste the token.

## Scopes and why

| Scope | Required | Used for |
|---|---|---|
| `read_products` | yes | Products, variants, prices, availability, price-change history |
| `read_orders` | yes | Orders, line items, discount applications, refunds |
| `read_all_orders` | recommended | Orders older than 60 days (trends, seasonality, repeat rate, year-over-year) |
| `read_discounts` | recommended | Verifying documented offers; coupon status and usage |
| `read_inventory` | recommended | Inventory item **unit cost** (COGS) and inventory tracking |
| `read_customers` | optional | Customer ID on orders for repeat-purchase analysis. Also requires *protected customer data* access (level 1) in the Dev Dashboard. Only the ID is stored. |

Not requested: any `write_*` scope. Write operations are not automated in this phase.

## What is synced

| Resource | Method | Incremental |
|---|---|---|
| Shop info, scopes | GraphQL query | every verify |
| Products + variants (+ unit cost) | bulk operation (JSONL) | `updated_at` watermark |
| Orders + line items + discount applications | bulk operation (JSONL) | `updated_at` watermark; first run limited to *first sync history* days |
| Refund lines | batched `nodes` query, refunded orders only | with orders |
| Code and automatic discounts | paginated queries | full each run (small) |

Not available from Shopify orders alone: **sessions/conversion** (import a traffic CSV),
**shipping cost to you, payment fees, fulfilment cost** (enter in store settings), and **unit costs**
if *Cost per item* is not maintained in Shopify (import a costs CSV).

## Disconnecting

Integrations → *Disconnect…* → type the store code. This deletes the encrypted credentials and token
cache from this application and stops scheduled syncs; optionally it also deletes all data synced
from Shopify for that store. To revoke access at the source, **uninstall the app** in Shopify admin
(or rotate the client secret in the Dev Dashboard).
