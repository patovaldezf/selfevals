"""CLI handlers for operational commands: skills, examples, serve, workers.

Split out of `commands.py` (which keeps the lifecycle/reporting handlers:
init, run, report, compare, estimate).
"""

from __future__ import annotations

import argparse
from importlib import resources
from pathlib import Path

from selfevals._errors import SelfEvalsUserError
from selfevals.cli._common import CommandError, _ensure_cwd_on_path
from selfevals.storage.factory import resolve_storage_url, storage_url_label


def cmd_skills_list(args: argparse.Namespace) -> int:
    from selfevals import skills

    names = skills.list_skills()
    if not names:
        print("(no bundled skills)")
        return 0
    for name in names:
        print(name)
    return 0


def cmd_skills_path(args: argparse.Namespace) -> int:
    from selfevals import skills

    try:
        path = skills.skill_path(args.name)
    except KeyError as exc:
        raise CommandError(str(exc)) from exc
    print(path)
    return 0


def cmd_skills_sync(args: argparse.Namespace) -> int:
    from selfevals import skills

    dest = Path(args.to) if args.to else skills.DEFAULT_SKILL_DEST
    only_consumer = not args.all
    written = skills.sync_skills(dest, only_consumer=only_consumer)
    scope = "consumer" if only_consumer else "all"
    if not written:
        print(f"skills already up to date in {dest} ({scope})")
        return 0
    print(f"synced {scope} skills to {dest} ({len(written)} file(s) written)")
    for path in written:
        print(f"  {path}")
    return 0


_EXAMPLE_NAMES = {"bakeoff", "pingpong", "route_ops_copilot", "sentiment_live", "showcase"}


def cmd_examples_copy(args: argparse.Namespace) -> int:
    name = args.name
    if name not in _EXAMPLE_NAMES:
        available = ", ".join(sorted(_EXAMPLE_NAMES))
        raise CommandError(f"unknown example {name!r}; available: {available}")

    target_root = Path(args.to)
    if target_root.exists() and not target_root.is_dir():
        raise CommandError(f"--to must be a directory: {target_root}")
    target_root.mkdir(parents=True, exist_ok=True)

    copied = _copy_example_tree(name=name, target_root=target_root)
    print(f"copied example {name!r} to {target_root}")
    for path in copied:
        print(f"  {path}")
    print("")
    print("Run (needs Postgres + Redis + a worker — `docker compose up -d`):")
    print(f"  selfevals run {target_root / 'evals' / 'experiments' / f'example_{name}.yaml'}")
    return 0


def _copy_example_tree(*, name: str, target_root: Path) -> list[Path]:
    source_root = resources.files("selfevals.examples").joinpath("evals")
    files = {
        source_root.joinpath("experiments", f"example_{name}.yaml"): target_root
        / "evals"
        / "experiments"
        / f"example_{name}.yaml",
        source_root.joinpath("datasets", f"{name}.jsonl"): target_root
        / "evals"
        / "datasets"
        / f"{name}.jsonl",
    }
    copied: list[Path] = []
    for source, dest in files.items():
        if not source.is_file():
            raise CommandError(f"packaged example file missing: {source}")
        if dest.exists():
            raise CommandError(f"refusing to overwrite existing file: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(dest)
    return copied


def _run_uvicorn(host: str, port: int, reload: bool) -> None:
    """Wrapper around uvicorn.run for testability — tests stub this."""
    try:
        import uvicorn
    except ImportError as exc:
        raise SelfEvalsUserError(
            "uvicorn is not installed. Install with: pip install 'selfevals[web]'"
        ) from exc
    uvicorn.run(
        "selfevals.api.app:build_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
        log_level="warning",
    )


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the FastAPI API (and optionally the SvelteKit web UI) in one command.

    Without this, dogfooding meant two terminals: `python -m selfevals.api`
    in one and `npm run dev` in another. The Altman/Musk filter from
    FRONTEND_PRODUCT_PLAN.md §3 — "a dev tries it in 5 min" — fails when
    the onboarding has two processes and a proxy. One command, one URL.

    Web wiring: SvelteKit's `adapter-node` build is a Node server (it
    does SSR); we can't serve it from FastAPI directly. Instead we spawn
    `node <web-dist>/index.js` as a child process with its own port (the
    API port + 1 by default), print both URLs, and tear it down cleanly
    when uvicorn exits or the user hits Ctrl+C.
    """
    import os
    import signal
    import subprocess
    from pathlib import Path

    # Put the cwd on sys.path so user/example entrypoints resolve from the API
    # too. The pairwise tournament endpoint resolves a `judge_entrypoint` (e.g.
    # `examples.hello_llm.agent:judge_pairwise`) by import — without this, a
    # tournament launched from the web UI 422s with "No module named 'examples'"
    # even though the same entrypoint works from `selfevals run` (which already
    # calls this). `sys.path` covers the in-process case; `PYTHONPATH` covers the
    # uvicorn `--reload` worker subprocess, which gets a fresh interpreter.
    _ensure_cwd_on_path()
    cwd = str(Path.cwd())
    existing_pp = os.environ.get("PYTHONPATH", "")
    if cwd not in existing_pp.split(os.pathsep):
        os.environ["PYTHONPATH"] = cwd + (os.pathsep + existing_pp if existing_pp else "")

    storage_url = resolve_storage_url(args.db)
    os.environ["SELFEVALS_STORAGE_URL"] = storage_url

    # Auto-detect a built web bundle if --web-dist wasn't given and the
    # user didn't disable web mode. Looks for `web/build/index.js`
    # relative to cwd — matches the conventional repo layout.
    web_dist: Path | None = None
    if not args.no_web:
        if args.web_dist:
            candidate = Path(args.web_dist)
            if not (candidate / "index.js").exists():
                raise SelfEvalsUserError(
                    f"--web-dist {candidate} does not contain index.js — "
                    f"run `npm run build` in the web/ dir first."
                )
            web_dist = candidate
        else:
            default_dist = Path.cwd() / "web" / "build"
            if (default_dist / "index.js").exists():
                web_dist = default_dist

    web_proc: subprocess.Popen[bytes] | None = None
    web_port = args.port + 1
    if web_dist is not None:
        web_env = os.environ.copy()
        web_env["PORT"] = str(web_port)
        web_env["ORIGIN"] = f"http://{args.host}:{web_port}"
        # The SvelteKit dev server proxies /api → 127.0.0.1:8000 via
        # vite.config.ts; the production build has no proxy, so without
        # this env var every `fetch('/api/...')` in +page.server.ts
        # would 404 against the Node server and the entire web becomes
        # unreachable (BUG-4). The hooks.server.ts handle intercepts
        # `/api/*` and forwards to this origin.
        web_env["SELFEVALS_API_BASE"] = f"http://{args.host}:{args.port}"
        try:
            web_proc = subprocess.Popen(
                ["node", str(web_dist / "index.js")],
                env=web_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise SelfEvalsUserError(
                "node is not installed but --web-dist was set. Install Node "
                "(https://nodejs.org) or pass --no-web to run the API alone."
            ) from exc

    # Tear down the web child cleanly on Ctrl+C / SIGTERM.
    def _shutdown(_signum: int, _frame: object | None) -> None:
        if web_proc is not None and web_proc.poll() is None:
            web_proc.terminate()
        raise KeyboardInterrupt

    prev_sigint = signal.signal(signal.SIGINT, _shutdown)
    prev_sigterm = signal.signal(signal.SIGTERM, _shutdown)

    print("selfevals serve")
    print(f"  API : http://{args.host}:{args.port}")
    if web_proc is not None:
        print(f"  Web : http://{args.host}:{web_port}")
    else:
        print("  Web : disabled (no build at web/build/index.js; pass --web-dist)")
    print(f"  DB  : {storage_url_label(storage_url)}")
    print("  ^C to stop.")

    try:
        _run_uvicorn(args.host, args.port, args.reload)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)
        if web_proc is not None and web_proc.poll() is None:
            web_proc.terminate()
            try:
                web_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                web_proc.kill()
    return 0


def cmd_worker_runs(args: argparse.Namespace) -> int:
    import logging
    import os

    # Imported lazily: worker.runs → api.run_launcher → cli → commands forms an
    # import cycle at module load. Deferring it to call time breaks the cycle.
    from selfevals.worker.runs import RunWorkerConfig, run_worker

    redis_url = args.redis_url
    if not redis_url:
        raise SelfEvalsUserError(
            "run worker requires Redis: pass --redis-url or set SELFEVALS_REDIS_URL"
        )
    # The worker is a long-lived process whose boot line is the only signal of
    # which Redis DB it bound to. The CLI configures no logging by default, so
    # without this those INFO lines are dropped on the floor. Only install a
    # handler if the root logger has none, to avoid clobbering external config.
    if not logging.getLogger().handlers:
        level = os.environ.get("SELFEVALS_LOG_LEVEL", "INFO").upper()
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    storage_url = resolve_storage_url(args.db)
    processed = run_worker(
        RunWorkerConfig(
            storage_url=storage_url,
            redis_url=redis_url,
            consumer=args.consumer,
            once=args.once,
        )
    )
    if args.once:
        print(f"processed run jobs: {processed}")
    return 0


def cmd_worker_sweeper(args: argparse.Namespace) -> int:
    import logging
    import os

    # Lazy import: same import-cycle reasoning as cmd_worker_runs.
    from selfevals.worker.lease_sweeper import LeaseSweeperConfig, run_lease_sweeper

    if not logging.getLogger().handlers:
        level = os.environ.get("SELFEVALS_LOG_LEVEL", "INFO").upper()
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    storage_url = resolve_storage_url(args.db)
    reaped = run_lease_sweeper(
        LeaseSweeperConfig(
            storage_url=storage_url,
            redis_url=args.redis_url,
            interval_seconds=args.interval,
            once=args.once,
        )
    )
    if args.once:
        print(f"swept expired-lease jobs: {reaped}")
    return 0
