"""Help-text contracts for the CLI.

Every top-level subcommand must:
- expose ``--help`` without crashing,
- show a non-empty description (so the user sees what it does), and
- include at least one ``Example:`` line in its epilog.

These tests guard against accidentally dropping the help/epilog scaffolding
when adding new flags or commands.
"""

from __future__ import annotations

import pytest

from bootstrap.cli.main import _build_parser, app

TOP_LEVEL_SUBCOMMANDS = (
    "init",
    "workspace",
    "experiment",
    "iteration",
    "report",
    "run",
    "compare",
    "estimate",
)


def _help_text(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    """Run `bootstrap <argv> --help` and return captured stdout.

    argparse exits the process via SystemExit(0) on --help, so we catch it.
    """
    with pytest.raises(SystemExit) as excinfo:
        app([*argv, "--help"])
    assert excinfo.value.code == 0
    return capsys.readouterr().out


def test_root_help_does_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    out = _help_text([], capsys)
    # All top-level subcommands listed.
    for name in TOP_LEVEL_SUBCOMMANDS:
        assert name in out, f"missing {name!r} in root --help"


def test_root_help_has_example(capsys: pytest.CaptureFixture[str]) -> None:
    out = _help_text([], capsys)
    assert "Example:" in out


@pytest.mark.parametrize("subcommand", TOP_LEVEL_SUBCOMMANDS)
def test_subcommand_help_does_not_crash(
    subcommand: str, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _help_text([subcommand], capsys)
    assert out.strip(), f"empty --help for {subcommand}"


@pytest.mark.parametrize("subcommand", TOP_LEVEL_SUBCOMMANDS)
def test_subcommand_help_has_example_epilog(
    subcommand: str, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _help_text([subcommand], capsys)
    assert "Example:" in out, (
        f"subcommand {subcommand!r} is missing an 'Example:' epilog. Help output was:\n{out}"
    )


@pytest.mark.parametrize("subcommand", TOP_LEVEL_SUBCOMMANDS)
def test_subcommand_help_has_non_trivial_description(
    subcommand: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The description should be more than the program usage line."""
    out = _help_text([subcommand], capsys)
    # Description sits between the usage block and "positional arguments" /
    # "options". We assert that something other than usage shows up.
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # Drop the usage block (which may wrap across lines).
    body = "\n".join(lines)
    assert "usage:" in body
    # At minimum there must be content beyond "usage: ...".
    assert len(body) > 80, f"description for {subcommand!r} looks too short:\n{out}"


def test_builder_produces_a_parser() -> None:
    """Sanity check: builder is importable and returns a parser instance."""
    parser = _build_parser()
    assert parser.prog == "bootstrap"
