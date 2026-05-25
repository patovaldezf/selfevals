"""Help-text helpers for the CLI.

Centralises the epilog formatting so every subcommand renders examples
the same way and so `tests/cli/test_help_texts.py` can assert a single
convention ("Example:" line) across the board.

Keep this module pure text. No business logic.
"""

from __future__ import annotations

import argparse
import textwrap
from collections.abc import Iterable


def epilog(*examples: str) -> str:
    """Render one or more shell examples as an argparse epilog.

    Each example is a single command line. The first is labelled
    ``Example:``; any additional ones are stacked underneath without a
    second label so the help text stays compact.
    """
    if not examples:
        raise ValueError("epilog() requires at least one example")
    lines = ["Example:"]
    lines.extend(f"  {ex}" for ex in examples)
    return "\n".join(lines)


def make_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    *,
    help_text: str,
    description: str | None = None,
    examples: Iterable[str] = (),
) -> argparse.ArgumentParser:
    """Add a subparser with a normalised description + epilog.

    - ``help_text`` is the one-liner shown in the parent ``--help`` listing.
    - ``description`` defaults to ``help_text`` and is shown at the top of
      the subcommand's own ``--help``.
    - ``examples`` becomes the epilog. Use the
      :class:`argparse.RawDescriptionHelpFormatter` so indentation
      survives.
    """
    example_list = list(examples)
    return subparsers.add_parser(
        name,
        help=help_text,
        description=textwrap.dedent(description or help_text).strip(),
        epilog=epilog(*example_list) if example_list else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
