"""`python -m bootstrap.api` — run the FastAPI app via uvicorn."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db",
        default=os.environ.get("BOOTSTRAP_DB", "./bootstrap.sqlite"),
        help="Path to the SQLite database file.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only).",
    )
    args = parser.parse_args(argv)
    os.environ["BOOTSTRAP_DB"] = args.db

    try:
        import uvicorn
    except ImportError as exc:
        print(
            "error: uvicorn is not installed. Install with: pip install bootstrap[web]",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    uvicorn.run(
        "bootstrap.api.app:build_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
