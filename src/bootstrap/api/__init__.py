"""HTTP bridge between the SQLite-backed storage and the web UI.

Read-only for MVP plus two writes: create workspace, queue experiment
spec. FastAPI is an optional extra (`pip install bootstrap[web]`);
importing this package does not import FastAPI eagerly so that the
default install path stays slim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app(*, db_path: str | None = None) -> FastAPI:
    """Build the FastAPI app. Defers the FastAPI import to call time."""
    from bootstrap.api.app import build_app

    return build_app(db_path=db_path)


__all__ = ["create_app"]
