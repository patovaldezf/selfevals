"""Tests for `selfevals serve`.

We don't actually start uvicorn in tests (would block); the cmd_serve
implementation delegates to `_run_uvicorn` which we stub. The web
subprocess uses a stubbed Popen so we don't need Node installed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from selfevals._errors import SelfEvalsUserError
from selfevals.cli import commands


def _ns(**overrides: Any) -> argparse.Namespace:
    """Build a serve-shaped namespace with sensible defaults."""
    base = {
        "db": "/tmp/x.sqlite",
        "host": "127.0.0.1",
        "port": 8000,
        "web_dist": None,
        "no_web": False,
        "reload": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_serve_disables_web_when_no_web_flag_set(tmp_path: Path) -> None:
    """--no-web suppresses subprocess.Popen entirely; uvicorn is the
    only thing that runs. Onboarding fallback for users without Node."""
    with (
        patch.object(commands, "_run_uvicorn") as fake_uv,
        patch("subprocess.Popen") as fake_popen,
    ):
        rc = commands.cmd_serve(_ns(no_web=True, db=str(tmp_path / "x.sqlite")))
    assert rc == 0
    fake_uv.assert_called_once_with("127.0.0.1", 8000, False)
    fake_popen.assert_not_called()


def test_serve_raises_when_uvicorn_missing() -> None:
    """Without `selfevals[web]` installed, `serve` must fail with a clear
    SelfEvalsUserError pointing at the install hint — not an uncaught
    ImportError traceback."""

    def fail_uvicorn(*_a: Any, **_kw: Any) -> None:
        raise SelfEvalsUserError(
            "uvicorn is not installed. Install with: pip install 'selfevals[web]'"
        )

    with (
        patch.object(commands, "_run_uvicorn", side_effect=fail_uvicorn),
        pytest.raises(SelfEvalsUserError, match="uvicorn"),
    ):
        commands.cmd_serve(_ns(no_web=True))


def test_serve_explicit_web_dist_must_have_index_js(tmp_path: Path) -> None:
    """If the user passes --web-dist <dir> and that dir has no index.js,
    fail with a clear hint to run `npm run build` — not silently fall
    back to API-only."""
    empty = tmp_path / "fakebuild"
    empty.mkdir()
    with (
        patch.object(commands, "_run_uvicorn"),
        pytest.raises(SelfEvalsUserError, match=r"index\.js"),
    ):
        commands.cmd_serve(_ns(web_dist=str(empty), db=str(tmp_path / "x.sqlite")))


def test_serve_spawns_node_with_correct_env(tmp_path: Path) -> None:
    """When --web-dist points at a valid build, we spawn the Node server
    with PORT=<api_port+1> and ORIGIN set, so SvelteKit's server fetch
    resolves relative URLs against the public host."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.js").write_text("// stub\n")

    with (
        patch.object(commands, "_run_uvicorn") as fake_uv,
        patch("subprocess.Popen") as fake_popen,
    ):
        fake_popen.return_value.poll.return_value = None
        fake_popen.return_value.terminate = MagicMock()
        fake_popen.return_value.wait = MagicMock()
        rc = commands.cmd_serve(
            _ns(web_dist=str(build_dir), port=8000, db=str(tmp_path / "x.sqlite"))
        )
    assert rc == 0
    fake_uv.assert_called_once()
    fake_popen.assert_called_once()
    call = fake_popen.call_args
    # First positional arg is the cmd vector.
    assert call.args[0][0] == "node"
    assert call.args[0][1] == str(build_dir / "index.js")
    env = call.kwargs["env"]
    assert env["PORT"] == "8001"
    assert env["ORIGIN"] == "http://127.0.0.1:8001"
    # BUG-4: without this, the Node server has no /api/* routes and the
    # hooks.server.ts proxy doesn't know where to forward. Test pins
    # the contract so a future refactor of cmd_serve can't drop it.
    assert env["SELFEVALS_API_BASE"] == "http://127.0.0.1:8000"
    # And the web subprocess gets terminated on shutdown.
    fake_popen.return_value.terminate.assert_called_once()


def test_serve_terminates_web_proc_when_uvicorn_raises(tmp_path: Path) -> None:
    """If uvicorn dies (port conflict, broken config, Ctrl+C), the Node
    child must still be terminated — no orphaned web server hogging port
    8001 between dev sessions."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.js").write_text("// stub\n")

    with (
        patch.object(commands, "_run_uvicorn", side_effect=KeyboardInterrupt()),
        patch("subprocess.Popen") as fake_popen,
    ):
        fake_popen.return_value.poll.return_value = None
        fake_popen.return_value.terminate = MagicMock()
        fake_popen.return_value.wait = MagicMock()
        rc = commands.cmd_serve(
            _ns(web_dist=str(build_dir), db=str(tmp_path / "x.sqlite"))
        )
    assert rc == 0
    fake_popen.return_value.terminate.assert_called_once()


def test_serve_sets_selfevals_db_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uvicorn factory loads the app fresh; the db path must reach the
    factory via SELFEVALS_DB. cmd_serve sets it before uvicorn.run."""
    monkeypatch.delenv("SELFEVALS_DB", raising=False)
    db_path = tmp_path / "set-by-serve.sqlite"
    with (
        patch.object(commands, "_run_uvicorn"),
        patch("subprocess.Popen"),
    ):
        commands.cmd_serve(_ns(no_web=True, db=str(db_path)))
    assert os.environ["SELFEVALS_DB"] == str(db_path)


def test_serve_node_missing_falls_back_clean(tmp_path: Path) -> None:
    """If node isn't installed but --web-dist was set, give a clear
    SelfEvalsUserError pointing at the fix, not an opaque FileNotFoundError."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.js").write_text("// stub\n")

    with (
        patch.object(commands, "_run_uvicorn"),
        patch("subprocess.Popen", side_effect=FileNotFoundError("no node")),
        pytest.raises(SelfEvalsUserError, match="node"),
    ):
        commands.cmd_serve(_ns(web_dist=str(build_dir), db=str(tmp_path / "x.sqlite")))
