"""Vercel entry point: the FastAPI app as a Python function.

The UI is served by Vercel as static files (frontend/dist); every /api request is routed here.
Database migrations run once per cold start (a no-op when the schema is current)."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from alembic import command
from alembic.config import Config

from app.main import create_app

command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
app = create_app()
