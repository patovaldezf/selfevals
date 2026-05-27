"""Shared exception hierarchy.

Two-tier model:

- `SelfEvalError`     — base for all selfeval-raised exceptions. Catch
  this at the outermost boundary to distinguish selfeval failures from
  truly internal Python errors (e.g. an OS-level disk-full).
- `SelfEvalUserError` — base for *user-correctable* failures: bad YAML,
  missing dataset, unknown grader, unreachable HTTP endpoint, locked
  database. These flow up to the CLI which prints a single-line
  `error: ...` and exits 2 (no stacktrace).

Everything else (assertion violations, programmer bugs, unexpected
Pydantic shapes that escape our friendly wrappers) keeps its stack
trace and yields exit 1 from the CLI dispatcher.

The CLI's `CommandError` is kept as a thin alias of `SelfEvalUserError`
for source compatibility with the rest of the package; both terms refer
to the same class so `except CommandError` and `except SelfEvalUserError`
behave identically.
"""

from __future__ import annotations


class SelfEvalError(Exception):
    """Root of selfeval's exception hierarchy."""


class SelfEvalUserError(SelfEvalError):
    """Raised for failures the user can fix without reading a traceback.

    The message must be self-contained: it is printed verbatim as
    `error: <message>` and the user gets no other context. Include the
    file path, the offending field, and (ideally) one concrete hint
    about how to fix it.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.hint = hint
        if hint:
            super().__init__(f"{message}\n  hint: {hint}")
        else:
            super().__init__(message)
