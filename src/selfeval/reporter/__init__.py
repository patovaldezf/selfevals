"""Reporter: render an OptimizationResult as markdown or JSON.

Two surfaces:

- `render_markdown(result)` produces a PR-comment-style summary:
  experiment header, per-iteration table, best-iteration callout,
  failure mode top-N, and termination reason.
- `render_json(result)` produces a stable, machine-readable
  serialization keyed on iteration index — the CLI dumps this when
  `--format json` is requested.

The reporter is pure: no I/O, no global state. Callers own where the
strings end up (stdout, a file, a GitHub comment).
"""

from __future__ import annotations

from selfeval.reporter.json_report import render_json
from selfeval.reporter.markdown import render_markdown

__all__ = ["render_json", "render_markdown"]
