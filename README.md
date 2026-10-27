# OffersHub

A private decision-support application for ecommerce stores, focused on **offers, discounts and order value (AOV)**. It uses real store data to find,
quantify, prioritise and validate growth opportunities across products, baskets, offers and stores,
while keeping every consequential change under your explicit control.

It answers five questions:

1. **What is happening across my stores?** — portfolio and store views, data freshness, alerts.
2. **Where are the biggest opportunities?** — evidence-based detectors, prioritised against each store's objectives.
3. **What exactly should I do?** — specific actions at store, product or offer level, with a recommended test.
4. **What is it worth, at what cost and risk?** — deterministic metrics and transparent scenario simulations with break-evens.
5. **Did it work?** — experiment tracking, evaluation that refuses to over-claim, and a learning record that feeds back into recommendations.

> Status: Phases 1–5 implemented and tested; Phase 6 (automated store writes) is deliberately
> limited to an approval workflow with manual application; Phase 7 (hosting) is prepared
> (auth, Postgres support, Docker) but not deployed. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Quick start (local)

Requirements: Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
# 1. Backend
cd backend
uv sync
cp ../.env.example .env
uv run python -m app.cli generate-key      # paste the output into EGI_SECRET_KEY in backend/.env
uv run python -m app.cli migrate           # creates backend/data/egi.db
uv run uvicorn app.main:app --port 8000    # API on http://127.0.0.1:8000 (docs at /api/docs)

# 2. Frontend (second terminal)
cd frontend
npm install
npm run dev                                # UI on http://localhost:5173 (proxies /api to :8000)
```

Single-process mode (what a hosted deployment runs): `cd frontend && npm run build`, then start
only the backend — it serves the built UI from `frontend/dist`.

### First steps in the app

1. **Explore safely**: Settings → *Create demo store*. The DEMO store is synthetic, labelled everywhere,
   excluded from real totals, and cannot be connected to a platform.
2. **Import your history**: Offers → *Import workbook*. Upload the offers-monitoring workbook (`.xlsx`)
   or its Events sheet (`.csv`). Preview first; every questionable row is listed with the reason.
3. **Connect a store**: Integrations → choose the store → enter the shop domain and credentials
   ([docs/SHOPIFY_SETUP.md](docs/SHOPIFY_SETUP.md)). The connection is verified against the live API
   before it is marked connected; then *Sync now*.
4. **Add what Shopify cannot provide**: Settings → *Unit costs CSV* and *Daily traffic CSV*;
   Stores → *Edit settings & objectives* for shipping cost, payment fees, gift cost and objectives.
5. **Analyse**: Opportunities → *Analyse*. Open any item to see evidence, assumptions, missing data,
   risks and the recommended test; *Plan as experiment* to start the lifecycle.

## Configuration

All configuration is environment-based (`backend/.env` or real environment variables, prefix `EGI_`).

| Variable | Default | Purpose |
|---|---|---|
| `EGI_SECRET_KEY` | — (required to store credentials) | Fernet key that encrypts integration credentials at rest. **Back it up separately from the database.** |
| `EGI_DATABASE_URL` | `sqlite:///backend/data/egi.db` | Use `postgresql+psycopg://…` when hosted. |
| `EGI_ENV` | `local` | `local` / `hosted` / `test`. `hosted` refuses to start without authentication. |
| `EGI_AUTH_MODE` | `none` | `password` for hosted use (signed HttpOnly session cookie). |
| `EGI_AUTH_PASSWORD` | — | Owner password when `EGI_AUTH_MODE=password`. |
| `EGI_SCHEDULER_ENABLED` | `true` | In-process automatic sync. Enable on exactly one process. |
| `EGI_SHOPIFY_API_VERSION` | `2026-10` | Shopify Admin API version (stable since 1 Oct 2026). |
| `EGI_ANTHROPIC_API_KEY` | — | Optional. Enables AI strategist commentary on recommendations. |
| `EGI_AI_MODEL` | `claude-opus-5-5` | Model used for commentary. |
| `EGI_LOG_LEVEL` / `EGI_LOG_JSON` | `INFO` / `false` | Logging; secrets are redacted from all log output. |

## Tests

```bash
cd backend && uv run pytest        # 98 tests: metrics, simulations, parsing, imports, offer status, Shopify client/sync
                                   # (against an emulated API), analytics, engine, experiments, approvals, HTTP API
uv run ruff check app tests
cd ../frontend && npm run build    # type-check + production build
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data model, data flow, design decisions
- [docs/METRICS.md](docs/METRICS.md) — every metric and simulation formula, and what each needs
- [docs/SHOPIFY_SETUP.md](docs/SHOPIFY_SETUP.md) — creating the app, scopes and why, disconnecting
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — backups, restore, migrations, hosted deployment
- [docs/ROADMAP.md](docs/ROADMAP.md) — phase status, known limitations, next steps
