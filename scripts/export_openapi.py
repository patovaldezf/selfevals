"""Export the FastAPI OpenAPI schema to JSON, for `web`'s `npm run gen:api`.

Builds the app in-process (no server, no real storage connection needed —
`build_app()` only opens storage lazily per-request) and writes its
`openapi()` schema to `web/openapi.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selfevals.api.app import build_app

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def main() -> None:
    app = build_app(db_path="postgresql://unused:unused@localhost/unused")
    schema = app.openapi()
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
