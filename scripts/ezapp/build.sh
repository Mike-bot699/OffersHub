#!/usr/bin/env bash
# EZ App build step: the UI, served by the API from frontend/dist.
set -euo pipefail
cd frontend && npm run build
