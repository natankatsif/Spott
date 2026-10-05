"""The API contract for the frontend: backend/openapi.json, generated from the FastAPI app and its models.

    cd backend && uv run python scripts/export_openapi.py           # rewrite openapi.json after an API change
    cd backend && uv run python scripts/export_openapi.py --check   # CI: fail if openapi.json is out of date

The frontend generates its TypeScript types from this file (frontend: npm run api:types) and checks its mocks
against it (npm run check:mocks), so it never reads backend code. Human-readable spec: docs/API.md.
"""

import argparse
import json
import sys
from pathlib import Path

from spott.api.main import app
from spott.api.schemas import ApiError

# Models the frontend uses that no route declares: every error body (handlers in spott/api/errors.py).
EXTRA = [ApiError]

OUT = Path(__file__).resolve().parents[1] / "openapi.json"


def render() -> str:
    spec = app.openapi()
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    for model in EXTRA:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    return json.dumps(spec, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if openapi.json differs from the code")
    args = ap.parse_args()
    text = render()
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"{OUT.name} is out of date: run `uv run python scripts/export_openapi.py` and commit it")
        print(f"{OUT.name} is up to date")
        return
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
