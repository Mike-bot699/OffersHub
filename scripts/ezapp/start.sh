#!/usr/bin/env bash
# EZ App start step: migrate, then serve UI + API on $PORT (0.0.0.0) in the foreground.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
: "${PORT:?PORT is set by the platform}"
# No silent defaults: the code directory is replaced on every deploy, so the database location
# must be set explicitly (a path that survives deploys, or a Postgres URL).
: "${EGI_DATABASE_URL:?Set EGI_DATABASE_URL (e.g. sqlite:////home/dev/offershub-data/egi.db)}"
case "$EGI_DATABASE_URL" in
  sqlite:///*) mkdir -p "$(dirname "${EGI_DATABASE_URL#sqlite:///}")" ;;
esac
export EGI_ENV="${EGI_ENV:-hosted}"
cd backend
uv run --no-dev python -m app.cli migrate
exec uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips='*'
