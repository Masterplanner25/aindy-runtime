from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "schema_version_baseline.json"
MODEL_ROOT = REPO_ROOT / "AINDY" / "db" / "models"
MEMORY_PERSISTENCE_PATH = REPO_ROOT / "AINDY" / "memory" / "memory_persistence.py"


def _configure_import_environment() -> None:
    defaults = {
        "DATABASE_URL": "sqlite://",
        "AINDY_ALLOW_SQLITE": "1",
        "SECRET_KEY": "schema-contract-check",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "DEEPSEEK_API_KEY": "ds-test-placeholder",
        "AINDY_API_KEY": "schema-contract-api-key",
        "PERMISSION_SECRET": "schema-contract-permission-secret",
        "ALLOWED_ORIGINS": "http://localhost:3000",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _iter_orm_files() -> list[Path]:
    files = sorted(MODEL_ROOT.rglob("*.py"))
    files.append(MEMORY_PERSISTENCE_PATH)
    return files


def _compute_orm_hash() -> str:
    digest = hashlib.sha256()
    for path in _iter_orm_files():
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\n")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def _load_schema_contract_version() -> str:
    _configure_import_environment()
    from AINDY.db.schema_contract import SCHEMA_CONTRACT_VERSION

    return SCHEMA_CONTRACT_VERSION


def _read_baseline() -> dict[str, str] | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _write_baseline(*, orm_hash: str, schema_contract_version: str) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(
            {
                "orm_hash": orm_hash,
                "schema_contract_version": schema_contract_version,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    orm_hash = _compute_orm_hash()
    schema_contract_version = _load_schema_contract_version()
    baseline = _read_baseline()

    if baseline is None:
        _write_baseline(
            orm_hash=orm_hash,
            schema_contract_version=schema_contract_version,
        )
        print("Baseline created.")
        return 0

    baseline_hash = baseline.get("orm_hash")
    baseline_version = baseline.get("schema_contract_version")

    if orm_hash == baseline_hash:
        return 0

    if schema_contract_version == baseline_version:
        print(
            "ORM models have changed but SCHEMA_CONTRACT_VERSION has not been updated. "
            "Update SCHEMA_CONTRACT_VERSION in AINDY/db/schema_contract.py before merging.",
            file=sys.stderr,
        )
        return 1

    _write_baseline(
        orm_hash=orm_hash,
        schema_contract_version=schema_contract_version,
    )
    print("Schema version baseline updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
