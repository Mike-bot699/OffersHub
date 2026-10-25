"""Command-line utilities.

  uv run python -m app.cli generate-key        # print a new EGI_SECRET_KEY
  uv run python -m app.cli migrate             # apply database migrations
  uv run python -m app.cli demo                # (re)build the synthetic demo store
  uv run python -m app.cli sync STORE_CODE     # run a synchronisation now
  uv run python -m app.cli analyse [STORE]     # regenerate recommendations
  uv run python -m app.cli backup              # SQLite online backup
  uv run python -m app.cli restore FILE        # restore a backup (stop the server first)
  uv run python -m app.cli import-offers PATH  # import the offers workbook (add --commit to write)
"""

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "generate-key":
        from app.core.security import generate_key

        print(generate_key())
        return 0
    if cmd == "migrate":
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(cfg, "head")
        return 0

    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import Store

    with session_scope() as db:
        if cmd == "demo":
            from app.services.demo import reset_demo
            from app.strategy.engine import run_store

            s = reset_demo(db)
            print(json.dumps(run_store(db, s)))
        elif cmd == "sync":
            from app.services.sync import run_sync

            store = db.scalar(select(Store).where(Store.code == args[0].upper()))
            job = run_sync(db, store.id, trigger="manual")
            print(json.dumps({"status": job.status, "stats": job.stats, "warnings": job.warnings}, default=str))
        elif cmd == "analyse":
            from app.strategy.engine import run_portfolio, run_store

            if args:
                store = db.scalar(select(Store).where(Store.code == args[0].upper()))
                print(json.dumps(run_store(db, store)))
            else:
                print(json.dumps(run_portfolio(db)))
        elif cmd == "backup":
            from app.services.backup import create_backup

            print(json.dumps(create_backup()))
        elif cmd == "restore":
            from app.services.backup import restore_backup

            print(json.dumps(restore_backup(args[0])))
        elif cmd == "import-offers":
            from dataclasses import asdict

            from app.services.offers_import import import_offers_workbook

            path = Path(args[0])
            rep = import_offers_workbook(db, path.read_bytes(), path.name, dry_run="--commit" not in args)
            out = asdict(rep)
            out["issues"] = out["issues"][:50]
            print(json.dumps(out, indent=1, default=str, ensure_ascii=False))
        else:
            print(f"Unknown command {cmd}\n{__doc__}")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
