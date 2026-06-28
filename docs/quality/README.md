# Quality Gates

`scripts/audit_technical_debt.py` tracks known technical-debt patterns and
compares them against `technical_debt_baseline.json`.

Use:

```bash
uv run python scripts/audit_technical_debt.py --fail-on-regression
```

Only update the baseline after a human review confirms that the new count is
intentional. Normal remediation should reduce counts.
