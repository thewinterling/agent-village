"""`villaged` entry point."""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta
from pathlib import Path

import uvicorn

from .daemon import VillageConfig
from .server import create_app
from .spool import DEFAULT_SPOOL
from .store import DEFAULT_DB
from .titling import TitlerConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent Village daemon.")
    parser.add_argument("--host", default="127.0.0.1", help="loopback only by default, and it should stay that way")
    # 8787 is popular enough that Cursor itself was found sitting on it. 8847 is not registered
    # with IANA and nothing else here wants it.
    parser.add_argument("--port", type=int, default=8847)
    parser.add_argument("--spool", type=Path, default=DEFAULT_SPOOL)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--backfill-hours", type=float, default=12.0)
    parser.add_argument("--no-history", action="store_true", help="run without the SQLite log")
    parser.add_argument(
        "--no-live-titles",
        action="store_true",
        help="do not ask Haiku (via the claude CLI) to keep each session's title current",
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )

    config = VillageConfig(
        spool_dir=args.spool,
        db_path=args.db,
        backfill_within=timedelta(hours=args.backfill_hours),
        enable_history=not args.no_history,
        titler=TitlerConfig(enabled=not args.no_live_titles),
    )
    app = create_app(config)

    print(f"\n  Agent Village  ->  http://{args.host}:{args.port}")
    print(f"  debug table    ->  http://{args.host}:{args.port}/table")
    print(f"  spool          ->  {args.spool}\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
