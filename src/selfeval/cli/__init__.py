"""SelfEval CLI.

A thin argparse-based command surface over the rest of the library.
Zero new runtime dependencies — Typer/Click would be friendlier but
each pulls a dep tree we don't need yet.

Entry point declared in `pyproject.toml`:
    selfeval = "selfeval.cli.main:app"
"""

from __future__ import annotations

from selfeval.cli.main import app

__all__ = ["app"]
