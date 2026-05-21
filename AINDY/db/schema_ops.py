from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from sqlalchemy import create_engine

from AINDY.config import settings
from AINDY.db.schema_contract import inspect_runtime_schema_payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m AINDY.db.schema_ops",
        description="Inspect the runtime-owned schema contract and current drift state.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inspect the current runtime-owned schema state without mutating the database.",
    )
    inspect_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output format.",
    )
    inspect_parser.add_argument(
        "--database-url",
        default="",
        help="Override DATABASE_URL for inspection.",
    )
    inspect_parser.add_argument(
        "--require-compatible",
        action="store_true",
        help="Exit non-zero when the schema is not compatible.",
    )
    return parser


def _render_text(payload: dict[str, object]) -> str:
    lines = [
        f"schema_contract_version: {payload['schema_contract_version']}",
        f"state: {payload['state']}",
        f"ok: {payload['ok']}",
        f"operator_action: {payload['operator_action']}",
        f"summary: {payload['summary']}",
        f"drift_classes: {', '.join(payload['drift_classes']) or 'none'}",
        "issues:",
    ]
    issues = payload.get("issues") or []
    if not issues:
        lines.append("  - none")
    else:
        for issue in issues:
            lines.append(
                "  - {code} table={table} column={column} remediation={remediation}: {detail}".format(
                    code=issue.get("code"),
                    table=issue.get("table") or "-",
                    column=issue.get("column") or "-",
                    remediation=issue.get("remediation_category") or "-",
                    detail=issue.get("detail") or "",
                )
            )
    lines.extend(
        [
            "inspection_entrypoint: "
            + str(payload["inspection"]["entrypoints"]["module"]),
            "offline_migration_required: "
            + str(payload["offline_migration_required"]),
        ]
    )
    return "\n".join(lines)


def inspect_command(*, database_url: str = "", output_format: str = "json") -> dict[str, object]:
    engine = create_engine(database_url or settings.DATABASE_URL)
    try:
        payload = inspect_runtime_schema_payload(engine)
    finally:
        engine.dispose()

    if output_format == "text":
        print(_render_text(payload))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command != "inspect":
        parser.error(f"Unsupported command {args.command!r}")

    payload = inspect_command(
        database_url=args.database_url,
        output_format=args.format,
    )
    if args.require_compatible and not payload.get("ok", False):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
