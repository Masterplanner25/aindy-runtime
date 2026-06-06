"""
check_env_example_coverage.py — Detect env var drift between code and AINDY/.env.example.

Reports variables that appear in AINDY/ source (via os.getenv() calls or Settings fields)
but are absent from AINDY/.env.example.

Exit codes:
  0 — no uncovered variables (or all are in the exclusion list)
  1 — uncovered variables found (advisory; see --strict to make this a hard failure)
  2 — internal error (e.g. parse failure)

Usage:
  python scripts/check_env_example_coverage.py             # advisory (exit 0 always unless error)
  python scripts/check_env_example_coverage.py --strict    # fail on any gap
  python scripts/check_env_example_coverage.py --verbose   # show all found vars
"""
from __future__ import annotations

import ast
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AINDY_ROOT = REPO_ROOT / "AINDY"
ENV_EXAMPLE = AINDY_ROOT / ".env.example"
CONFIG_PY = AINDY_ROOT / "config.py"

# Variables that are intentionally absent from .env.example.
# Grouped by reason so future maintainers understand each exclusion.
EXCLUSIONS: frozenset[str] = frozenset({
    # Test harness — only meaningful inside pytest
    "PYTEST_CURRENT_TEST",
    "TESTING",
    "TEST_MODE",
    "AINDY_TEST_STRICT_SYSTEM_EVENTS",
    "AINDY_DEBUG_SYSTEM_EVENTS",
    "ENFORCE_EXECUTION_CONTRACT",
    "AINDY_ASYNC_HEAVY_EXECUTION",
    # OS / system
    "HOSTNAME",
    "PATH",
    "SYSTEMROOT",
    "INSTANCE_ID",
    # Deprecated aliases — present in .env.example as comments
    "AINDY_REDIS_URL",
    "AINDY_STUCK_RUN_THRESHOLD_MINUTES",
    # Infrastructure / Docker Compose only (not runtime Settings fields)
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "MONGO_INITDB_ROOT_USERNAME",
    "MONGO_INITDB_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    # Computed / internal constants set by the runtime itself, not operators
    "VERSION",
    "API_VERSION",
    "API_MIN_CLIENT_VERSION",
    # Set by the compose file / container runtime, not operators
    "AINDY_ENV_FILE",
    "AINDY_HOST",
})


def _collect_os_getenv_vars(aindy_root: Path) -> set[str]:
    """AST-parse all .py files under aindy_root; return os.getenv() string literal args."""
    found: set[str] = set()
    for py_file in aindy_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match os.getenv("VAR") and os.environ.get("VAR")
            is_os_getenv = (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            )
            is_environ_get = (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
            )
            # Also match os.environ["VAR"] subscript access
            if is_os_getenv or is_environ_get:
                if node.args and isinstance(node.args[0], ast.Constant):
                    val = node.args[0].value
                    if isinstance(val, str):
                        found.add(val)
    return found


def _collect_settings_fields(config_py: Path) -> set[str]:
    """Extract field names from the Settings Pydantic model in config.py."""
    found: set[str] = set()
    try:
        tree = ast.parse(config_py.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return found

    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "Settings"):
            continue
        for item in ast.walk(node):
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                # Pydantic Settings maps field name → env var name.
                # The env var name is typically the uppercased field name.
                found.add(item.target.id.upper())
    return found


def _collect_env_example_vars(env_example: Path) -> set[str]:
    """Parse .env.example; return all variable names, commented-out or not."""
    found: set[str] = set()
    # Match: optional leading whitespace + optional '#' + optional spaces + VAR_NAME=
    pattern = re.compile(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)\s*=", re.MULTILINE)
    text = env_example.read_text(encoding="utf-8", errors="replace")
    for m in pattern.finditer(text):
        found.add(m.group(1))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any uncovered variable (makes the check a hard failure).")
    parser.add_argument("--verbose", action="store_true", help="Print all discovered variables, not just gaps.")
    args = parser.parse_args()

    if not ENV_EXAMPLE.exists():
        print(f"error: {ENV_EXAMPLE} not found", file=sys.stderr)
        return 2
    if not CONFIG_PY.exists():
        print(f"error: {CONFIG_PY} not found", file=sys.stderr)
        return 2

    try:
        getenv_vars = _collect_os_getenv_vars(AINDY_ROOT)
        settings_fields = _collect_settings_fields(CONFIG_PY)
        example_vars = _collect_env_example_vars(ENV_EXAMPLE)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    all_code_vars = getenv_vars | settings_fields
    uncovered = sorted(
        v for v in all_code_vars
        if v not in example_vars and v not in EXCLUSIONS
    )

    if args.verbose:
        print(f"os.getenv() vars found:  {len(getenv_vars)}")
        print(f"Settings fields found:   {len(settings_fields)}")
        print(f"Total unique code vars:  {len(all_code_vars)}")
        print(f".env.example vars:       {len(example_vars)}")
        print(f"Exclusion list entries:  {len(EXCLUSIONS)}")
        print()

    if uncovered:
        print(f"[env-coverage] {len(uncovered)} variable(s) in AINDY/ code not in AINDY/.env.example:")
        for v in uncovered:
            source = "os.getenv" if v in getenv_vars else "Settings field"
            print(f"  {v}  ({source})")
        print()
        if args.strict:
            print("[env-coverage] FAIL (--strict mode)")
            return 1
        print("[env-coverage] ADVISORY — add entries to AINDY/.env.example or EXCLUSIONS in this script.")
        return 0

    covered = len(all_code_vars) - len(uncovered)
    print(f"[env-coverage] OK — {covered} code vars covered by AINDY/.env.example or exclusion list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
