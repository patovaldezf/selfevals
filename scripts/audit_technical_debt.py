#!/usr/bin/env python3
"""Audit known selfevals technical-debt patterns.

The audit is intentionally baseline-driven: existing debt is tracked, and CI
fails when a change increases it. Use ``--update-baseline`` only after a human
has reviewed and accepted a new baseline.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "quality" / "technical_debt_baseline.json"

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
    ".agents",
}
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".svelte",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
CODE_PREFIXES = ("src/", "tests/", "web/src/", "landing/src/", "examples/")
LARGE_FILE_LIMITS = {
    ".py": 700,
    ".svelte": 700,
    ".ts": 900,
    ".tsx": 700,
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "detail": self.detail}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        files.append(path)
    return sorted(files)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def audit_large_files(files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        limit = LARGE_FILE_LIMITS.get(path.suffix)
        if limit is None:
            continue
        lines = read_text(path).count("\n") + 1
        if lines > limit:
            findings.append(Finding(_rel(path), 1, f"{lines} lines > limit {limit}"))
    return findings


def audit_large_python_functions(files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        if path.suffix != ".py":
            continue
        source = read_text(path)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            end = getattr(node, "end_lineno", node.lineno)
            size = end - node.lineno + 1
            limit = 180 if isinstance(node, ast.ClassDef) else 120
            if size > limit:
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                name = getattr(node, "name", "<unknown>")
                header = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else name
                findings.append(
                    Finding(_rel(path), node.lineno, f"{kind} {name} has {size} lines: {header}")
                )
    return findings


def audit_regex(files: list[Path], name: str, pattern: str) -> list[Finding]:
    rx = re.compile(pattern)
    findings: list[Finding] = []
    for path in files:
        rel = _rel(path)
        if not rel.startswith(CODE_PREFIXES):
            continue
        text = read_text(path)
        for match in rx.finditer(text):
            findings.append(Finding(rel, line_number(text, match.start()), name))
    return findings


def audit_direct_frontend_fetch(files: list[Path]) -> list[Finding]:
    allowed = {
        "web/src/lib/api/client.ts",
        "web/src/lib/api/request.ts",
        "web/src/lib/api/sse.ts",
        "web/src/hooks.server.ts",
    }
    findings: list[Finding] = []
    for path in files:
        rel = _rel(path)
        if not rel.startswith("web/src/") or path.suffix not in {".ts", ".svelte"}:
            continue
        if rel in allowed:
            continue
        text = read_text(path)
        for match in re.finditer(r"\bfetch\s*\(", text):
            findings.append(Finding(rel, line_number(text, match.start()), "direct fetch bypasses API client"))
    return findings


def audit_direct_user_header(files: list[Path]) -> list[Finding]:
    allowed = {
        "src/selfevals/api/auth.py",
        "src/selfevals/api/app.py",
        "web/src/lib/api/client.ts",
        "web/src/lib/api/request.ts",
    }
    findings: list[Finding] = []
    for path in files:
        rel = _rel(path)
        if rel in allowed or not (rel.startswith("src/") or rel.startswith("web/src/")):
            continue
        text = read_text(path)
        for match in re.finditer(r"X-SelfEvals-User", text):
            findings.append(Finding(rel, line_number(text, match.start()), "direct auth header usage"))
    return findings


def audit_docs_version(files: list[Path]) -> list[Finding]:
    pyproject = read_text(ROOT / "pyproject.toml")
    match = re.search(r'^version = "([^"]+)"', pyproject, flags=re.MULTILINE)
    current = match.group(1) if match else ""
    findings: list[Finding] = []
    if not current:
        return findings
    version_rx = re.compile(r"\bversion\s+0\.\d+\.\d+\b|\bcurrent version `?0\.\d+\.\d+`?", re.I)
    for path in files:
        rel = _rel(path)
        if path.suffix != ".md" or rel == "docs/TECHNICAL_DEBT.md":
            continue
        text = read_text(path)
        for match in version_rx.finditer(text):
            if current not in match.group(0):
                findings.append(Finding(rel, line_number(text, match.start()), "stale version reference"))
    return findings


def collect() -> dict[str, list[dict[str, Any]]]:
    files = iter_files()
    audits = {
        "large_files": audit_large_files(files),
        "large_python_symbols": audit_large_python_functions(files),
        "broad_exception_catches": audit_regex(files, "broad except Exception", r"except Exception\b"),
        "type_ignores": audit_regex(files, "type ignore", r"#\s*type:\s*ignore"),
        "json_extract": audit_regex(files, "json_extract usage", r"\bjson_extract\b"),
        "list_entities_calls": audit_regex(files, "list_entities call", r"\blist_entities\s*\("),
        "get_entity_calls": audit_regex(files, "get_entity call", r"\bget_entity\s*\("),
        "direct_frontend_fetch": audit_direct_frontend_fetch(files),
        "direct_user_header": audit_direct_user_header(files),
        "docs_version_drift": audit_docs_version(files),
    }
    return {name: [finding.as_dict() for finding in findings] for name, findings in audits.items()}


def summarize(report: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {name: len(findings) for name, findings in sorted(report.items())}


def load_baseline() -> dict[str, Any]:
    if not BASELINE.exists():
        return {"counts": {}, "findings": {}}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def write_baseline(report: dict[str, list[dict[str, Any]]]) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": "Baseline for scripts/audit_technical_debt.py. Decrease counts as debt is removed; do not increase without review.",
        "counts": summarize(report),
        "findings": report,
    }
    BASELINE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--update-baseline", action="store_true", help="Rewrite the tracked baseline.")
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Fail when any measured category exceeds the tracked baseline.",
    )
    args = parser.parse_args(argv)

    report = collect()
    counts = summarize(report)

    if args.update_baseline:
        write_baseline(report)

    if args.json:
        print(json.dumps({"counts": counts, "findings": report}, indent=2, sort_keys=True))
    else:
        for name, count in counts.items():
            print(f"{name}: {count}")

    if args.fail_on_regression:
        baseline = load_baseline()
        baseline_counts = baseline.get("counts", {})
        regressions = []
        for name, count in counts.items():
            allowed = int(baseline_counts.get(name, 0))
            if count > allowed:
                regressions.append((name, count, allowed))
        if regressions:
            print("\nTechnical debt regression detected:", file=sys.stderr)
            for name, count, allowed in regressions:
                print(f"- {name}: {count} > baseline {allowed}", file=sys.stderr)
            print("Run with --json for details, then fix the regression or update the baseline after review.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
