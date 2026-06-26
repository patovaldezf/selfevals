"""`python -m selfevals.api` — run the FastAPI app via uvicorn."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="selfevals-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db",
        default=None,
        help="Postgres storage URL. Defaults to SELFEVALS_STORAGE_URL.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only).",
    )
    args = parser.parse_args(argv)
    if args.db is not None:
        os.environ["SELFEVALS_STORAGE_URL"] = args.db

    try:
        import uvicorn
    except ImportError as exc:
        print(
            "error: uvicorn is not installed. Install with: pip install selfevals[web]",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    # uvicorn's default log config sets `disable_existing_loggers`, which
    # silences `selfevals.*` loggers — including the run-launcher's orphan-job
    # WARNING. Configure the root logger ourselves and tell uvicorn to leave
    # logging alone (`log_config=None`) so app and server logs share one stream.
    import logging

    level = os.environ.get("SELFEVALS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    uvicorn.run(
        "selfevals.api.app:build_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
        log_config=None,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
