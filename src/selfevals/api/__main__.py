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
        help=(
            "SQLite path or storage URL. Defaults to SELFEVALS_STORAGE_URL, "
            "then SELFEVALS_DB, then ./selfevals.sqlite."
        ),
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only).",
    )
    args = parser.parse_args(argv)
    if args.db is not None:
        os.environ["SELFEVALS_DB"] = args.db
        os.environ["SELFEVALS_STORAGE_URL"] = args.db

    try:
        import uvicorn
    except ImportError as exc:
        print(
            "error: uvicorn is not installed. Install with: pip install selfevals[web]",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    uvicorn.run(
        "selfevals.api.app:build_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
