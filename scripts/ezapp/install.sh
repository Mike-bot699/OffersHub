#!/usr/bin/env bash
# EZ App install step: Python (via uv, pinned) for the API, npm for the UI.
set -euo pipefail
UV_VERSION=0.11.32
export PATH="$HOME/.local/bin:$PATH"
if ! uv --version 2>/dev/null | grep -q "$UV_VERSION"; then
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi
(cd backend && uv sync --frozen --no-dev)   # also installs Python 3.13 if the container lacks it
(cd frontend && npm ci)
