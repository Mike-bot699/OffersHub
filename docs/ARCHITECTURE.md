# Architecture

## Overview

```
 Browser (React SPA)  ──/api──▶  FastAPI app
                                   ├─ api/            HTTP routes, validation, auth middleware, error mapping
                                   ├─ services/       integrations, sync engine, scheduler, imports, demo, backup, audit
                                   ├─ connectors/     platform-neutral contract + Shopify implementation
                                   ├─ analytics/      dataset loader, metrics, store/product/basket analysis, simulations
                                   ├─ strategy/       detectors, prioritisation engine, frameworks, optional AI layer
                                   ├─ offers/         offer classification, verification, monitoring
                                   ├─ experiments/    lifecycle + evaluation + learning
                                   ├─ actions/        approval workflow for store changes
                                   └─ models/ + db/   SQLAlchemy models, sessions; Alembic migrations
                                          │
                               SQLite (local) / PostgreSQL (hosted)
```

**Stack**: Python 3.13, FastAPI, SQLAlchemy 2, Alembic, APScheduler, httpx, cryptography (Fernet),
openpyxl; React 19 + TypeScript + Vite, TanStack Query, Recharts. Python was chosen because the
core value is analytics and data handling; one FastAPI process serves both the API and the built UI,
which keeps local use and hosting identical.

## Principles enforced in code

| Principle | Where |
|---|---|
| No cross-store contamination | Every fact table has `store_id`; `analytics/dataset.load_dataset` loads exactly one store; portfolio totals are summed **per currency** and exclude demo stores; importers match rows within one store only. |
| No fabricated data or status | An integration is `connected` only after a live API call (`services/integrations.verify`). Demo data lives in stores flagged `is_demo`, labelled in every view and API response. |
| Provenance | `source` on products, variants, orders, costs, coupons, events, traffic (`shopify`, `csv_import`, `spreadsheet_import`, `manual`, `assumption`, `demo`, `derived`). Imports keep `source_ref`, `import_batch_id`, SHA-256. |
| Missing data is explicit | Metric functions return `None` rather than 0; margins require ≥95% cost coverage; contribution lists missing cost components; detectors put gaps in `missing_data`. |
| Documented ≠ verified | `PromoEvent.verification_status` (`documented_only`, `verified_in_store`, `mismatch`, `not_found_in_store`, `unverifiable`). Mismatches are reported only when the store's discounts have been synced. |
| Exposure ≠ incremental | Detectors label estimates as scenarios or exposure; before/after experiments are capped at *weak* evidence. |
| Secrets never leave the backend | Fernet-encrypted credentials; `api/common.to_dict` strips secret columns; `core/security.redact` scrubs logs and error messages. |
| No silent writes | `actions/workflow`: proposals with current/proposed values, typed approval, staleness check, idempotency key, audit; automated execution disabled. |

## Data model (main tables)

- **stores**, **store_objectives** (ordered objectives with params), **app_settings**
- **integrations** (encrypted credentials, token cache, scopes, probe results, sync settings),
  **sync_states** (watermark per resource), **sync_jobs**, **import_batches**
- **products**, **variants**, **price_history** (observed changes), **cost_records** (effective-dated, with source)
- **orders**, **order_lines** (original price, allocated discounts, refunded qty/amount), **customers** (pseudonymous ID only)
- **traffic_daily** (sessions/funnel, store or product level)
- **offers** (reusable mechanics), **promo_events** (dated executions with targets, recorded results, verification, data issues), **coupons**
- **recommendations** (full structured recommendation + score breakdown + prior evidence + optional AI commentary), **simulations**, **metric_snapshots**
- **experiments** (lifecycle, design, arms, evaluation, decision, history), **learning_records**
- **action_proposals** (approval workflow), **audit_log**

## Synchronisation

`services/sync.run_sync` per store, guarded by a per-store lock:

1. Build the connector from encrypted credentials; read granted scopes → capabilities.
2. **Products** and **orders** via Shopify bulk operations (JSONL, no per-query cost limit);
   incremental with `updated_at:>watermark − 10 min`; first run bounded by `initial_history_days`.
3. Refund line detail fetched only for refunded orders (batched `nodes` query).
4. **Discounts** via paginated code + automatic discount queries; disappeared codes marked deleted.
5. Each resource commits independently; watermarks advance only on success; job status
   `success` / `partial` / `failed`; missing scopes become warnings.

The GraphQL client handles cost-based throttling (pre-emptive pacing and `THROTTLED` retries),
429/5xx with backoff, 401 token refresh for client-credentials apps, and 403/ACCESS_DENIED as
capability errors. On connect, a *probe* runs miniature versions of every sync query so a schema
or permission problem appears immediately rather than in an overnight job.

## Recommendation engine

`strategy/engine.run_store` builds a context (store summary, products, co-purchase, basket, gift
candidates, offer history, alerts, objectives), runs each detector in isolation, applies learning
records (only *strong/moderate* evidence may change confidence), scores, and upserts by fingerprint
(user status such as *dismissed* is preserved; unsupported items become *stale*).

Score = (0.45·impact + 0.25·evidence + 0.15·feasibility + 0.15·(1 − risk)) × objective alignment × 100,
where impact is normalised to 3% of annualised net sales, alignment is ×1.3 / ×1.15 for primary /
secondary objective support and ×0.6 for conflicts. Data gaps that block analysis are floored at 70.

The optional AI layer (`strategy/llm.py`) sends only the recommendation's computed evidence to Claude
and stores its critique separately, labelled as hypotheses.

## Extending

- **New platform**: implement `connectors/base.Connector` (verify, capabilities, fetch_products,
  fetch_orders, fetch_refund_lines, fetch_discounts) returning the DTOs; the sync service and all
  analytics work unchanged.
- **New data source** (analytics, email): add a connector or importer that writes `traffic_daily`
  or a new provenance-tagged table.
- **New detector**: a function `Context -> list[RecDraft]` appended to `DETECTORS`.
- **Write operations**: add an executor per `ACTION_KINDS` entry once scopes, live verification and
  rollback are tested (see ROADMAP).
