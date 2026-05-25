"""Translate low-level exceptions into actionable CLI errors.

This module is the single chokepoint between "what the runtime raised"
and "what the user sees on stderr". The rule is:

* If the failure is something a user can fix by changing inputs,
  surfaces, or configuration, we wrap the underlying exception in a
  :class:`BootstrapUserError` with a tight, file-relative message and
  (when possible) a concrete hint.
* If it's an internal invariant violation, we re-raise so the traceback
  reaches the user.

Adding a new friendly-error path: pick a function below or add one. Do
**not** sprinkle ``except FooError`` blocks across the CLI — keeping
the translation table here is the whole point.
"""

from __future__ import annotations

import difflib
import sqlite3
from pathlib import Path
from urllib.error import HTTPError, URLError

import yaml

from bootstrap._errors import BootstrapUserError
from bootstrap.repo.loader import LoaderError, load_experiment_spec
from bootstrap.runner.adapters import AdapterError

if False:  # for type checkers only, no runtime cycle.
    from bootstrap.repo.loader import ExperimentSpec


def load_spec(path: str | Path, *, workspace_id: str | None = None) -> ExperimentSpec:
    """Load a YAML experiment spec with friendly error messages.

    Wraps :func:`bootstrap.repo.loader.load_experiment_spec`. Catches
    raw YAML parser errors and `LoaderError` and re-raises them as
    :class:`BootstrapUserError` so the CLI prints a clean single line.

    The loader already constructs nice messages for *most* failure
    modes; this wrapper exists so callers don't have to know about
    `LoaderError` and so the few classes of error the loader does not
    label (a `yaml.YAMLError` leaking through, a vanished file race)
    get the same one-line treatment.
    """
    spec_path = Path(path)
    try:
        return load_experiment_spec(spec_path, workspace_id=workspace_id)
    except LoaderError as exc:
        # `LoaderError` is the loader's friendly umbrella, but the dataset
        # branch deserves the special "did you mean ..." treatment so we
        # intercept it before falling through to the generic hint table.
        dataset = _missing_dataset_path(exc)
        if dataset is not None:
            err = dataset_not_found(dataset)
            raise err from exc
        raise BootstrapUserError(str(exc), hint=_yaml_hint_if_relevant(spec_path, exc)) from exc
    except yaml.YAMLError as exc:  # pragma: no cover - loader already wraps this
        raise BootstrapUserError(
            f"could not parse YAML {spec_path}: {exc}",
            hint="check indentation and quoting; run `yamllint` for a line-by-line view",
        ) from exc
    except FileNotFoundError as exc:  # pragma: no cover - loader already handles
        raise BootstrapUserError(f"experiment spec not found: {spec_path}") from exc


def dataset_not_found(path: Path) -> BootstrapUserError:
    """Build a `Dataset not found` error with a fuzzy-match suggestion.

    Returns the exception; the caller raises (lets the caller pick
    `raise ... from exc` to preserve a stacktrace if it has one).
    """
    parent = path.parent if path.parent.exists() else Path()
    candidates: list[str] = []
    if parent.exists():
        for entry in parent.iterdir():
            if entry.is_file() and entry.suffix in {".jsonl", ".json", ".yaml", ".yml"}:
                candidates.append(entry.name)
    closest = difflib.get_close_matches(path.name, candidates, n=1, cutoff=0.6)
    hint: str | None = None
    if closest:
        hint = f"did you mean {parent / closest[0]}?"
    return BootstrapUserError(f"dataset path {str(path)!r} not found", hint=hint)


def unknown_grader(name: str, available: list[str]) -> BootstrapUserError:
    """`Grader 'foo' not registered. Available: ...`."""
    available_str = ", ".join(sorted(available)) if available else "(none)"
    closest = difflib.get_close_matches(name, available, n=1, cutoff=0.6)
    hint: str | None = None
    if closest:
        hint = f"did you mean {closest[0]!r}?"
    return BootstrapUserError(
        f"grader {name!r} not registered; available: {available_str}",
        hint=hint,
    )


def wrap_adapter_error(exc: Exception, *, url: str | None = None) -> BootstrapUserError:
    """Convert an `AdapterError` / `URLError` / `HTTPError` into a user error.

    `url` is the endpoint the adapter was POSTing to, when known. The
    message format is stable so docs/troubleshooting.md can cite it.
    """
    target = f" to {url}" if url else ""
    if isinstance(exc, HTTPError):
        return BootstrapUserError(
            f"HTTP adapter got {exc.code} {exc.reason}{target}",
            hint="check the endpoint returns 2xx with a JSON body",
        )
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", exc)
        return BootstrapUserError(
            f"HTTP adapter could not reach{target} ({reason})",
            hint="confirm the endpoint is running and reachable from this host",
        )
    if isinstance(exc, TimeoutError):
        return BootstrapUserError(
            f"HTTP adapter timed out{target}",
            hint="increase timeout_seconds or check endpoint responsiveness",
        )
    # `AdapterError` covers contract violations (bad JSON, non-dict, etc.).
    return BootstrapUserError(f"adapter error{target}: {exc}")


def wrap_sqlite_error(exc: sqlite3.Error, *, db_path: Path | str) -> BootstrapUserError:
    """Turn a raw `sqlite3.OperationalError` into something a human can act on."""
    msg = str(exc).lower()
    if "locked" in msg or "busy" in msg:
        return BootstrapUserError(
            f"sqlite database {db_path} is locked",
            hint="another bootstrap process is using it; try `--db <new-path>` or wait",
        )
    if "malformed" in msg or "corrupt" in msg or "not a database" in msg:
        return BootstrapUserError(
            f"sqlite database {db_path} is corrupted or not a valid bootstrap db",
            hint="back up the file and re-run with `--db <new-path>` to start clean",
        )
    return BootstrapUserError(f"sqlite error at {db_path}: {exc}")


# --- internals ---


def _missing_dataset_path(exc: LoaderError) -> Path | None:
    """If the LoaderError comes from `_read_jsonl`'s 'dataset file not found',
    return the missing path so the caller can add a fuzzy hint."""
    msg = str(exc)
    marker = "dataset file not found: "
    if marker not in msg:
        return None
    # Format: "dataset file not found: <path>"
    return Path(msg.split(marker, 1)[1].strip())


def _yaml_hint_if_relevant(spec_path: Path, exc: LoaderError) -> str | None:
    msg = str(exc).lower()
    if "could not parse yaml" in msg:
        return (
            f"open {spec_path} and check indentation and unclosed brackets; "
            "yaml errors usually point at the line just *after* the mistake"
        )
    if "workspace_id missing" in msg:
        return "add `workspace: ws_<id>` at the top of the file or pass --workspace"
    if "missing or non-mapping `experiment:`" in msg:
        return "the YAML must have an `experiment:` key with the experiment block"
    if "dataset" in msg and "not found" in msg:
        return "check `dataset.cases_path` is relative to the YAML file"
    if "entrypoint" in msg:
        return "format must be 'package.module:callable_name' (note the colon)"
    return None


__all__ = [
    "AdapterError",
    "dataset_not_found",
    "load_spec",
    "unknown_grader",
    "wrap_adapter_error",
    "wrap_sqlite_error",
]
