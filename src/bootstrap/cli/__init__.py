"""Bootstrap CLI.

A thin argparse-based command surface over the rest of the library.
Zero new runtime dependencies — Typer/Click would be friendlier but
each pulls a dep tree we don't need yet.

Entry point declared in `pyproject.toml`:
    bootstrap = "bootstrap.cli.main:app"
"""

from __future__ import annotations

from bootstrap.cli.main import app

__all__ = ["app"]
