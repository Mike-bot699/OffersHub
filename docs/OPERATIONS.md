# Operations

## Database migrations

```bash
cd backend
uv run python -m app.cli migrate                       # upgrade to latest
uv run alembic revision --autogenerate -m "describe"   # after changing models (review the file!)
```

## Backups

What to back up — both are needed for a full recovery:

1. **The database** (`backend/data/egi.db` locally, or the PostgreSQL database).
2. **`EGI_SECRET_KEY`** — kept separately (password manager). Without it, stored integration
   credentials cannot be decrypted; everything else still works and integrations can be reconnected.

SQLite (safe while the app runs; consistent online copy + integrity check):

```bash
uv run python -m app.cli backup            # → backend/data/backups/egi-YYYYmmdd-HHMMSS.db
```

or Settings → Backups → *Create backup now*. Copy `backend/data/backups/` off the machine regularly.

Restore (stop the server first; a safety backup of the current state is taken automatically):

```bash
uv run python -m app.cli restore egi-20261025-101500.db
```

PostgreSQL: `pg_dump -Fc "$DATABASE_URL" > egi.dump` and `pg_restore -d "$DATABASE_URL" --clean egi.dump`.

## Data retention and privacy

- Customer data is limited to Shopify's customer ID and first-order date. No names, emails, phone numbers or addresses are requested or stored.
- *Disconnect → also delete synced data* removes all Shopify-sourced records for that store.
- Deleting a store cascades to all of its data.
- Logs never contain credentials (redaction is applied to every log line and error message).

## Moving from local to hosted (Phase 7 — prepared, not yet deployed)

The same code runs hosted; only environment changes:

1. Provision PostgreSQL and set `EGI_DATABASE_URL=postgresql+psycopg://…` (add `psycopg[binary]` to dependencies).
2. Set `EGI_ENV=hosted`, `EGI_AUTH_MODE=password`, a strong `EGI_AUTH_PASSWORD`, the **same**
   `EGI_SECRET_KEY` as local (so credentials remain decryptable), and `EGI_CORS_ORIGINS` if the UI is served elsewhere.
   The app refuses to start in hosted mode without authentication.
3. Run migrations, then move data: export local SQLite and load into Postgres (e.g. `pgloader`), or
   start fresh and re-sync from Shopify (all synced data is reproducible; imports and manual records are not).
4. Build: `docker build -t egi .` (Dockerfile at the repository root builds the UI and serves it from the API).
5. Serve over HTTPS only (session cookies are `Secure` in hosted mode). Run one instance with
   `EGI_SCHEDULER_ENABLED=true`; any additional instances must set it to `false`.
6. Verify: `/api/health`, login, *Test connection* on each store, a manual sync, and a backup/restore drill.
