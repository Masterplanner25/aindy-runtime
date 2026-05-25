"""
Enforcement test for the AINDY.platform_layer public import boundary.

The three assertions here form a triangle that prevents the three sources of
truth from drifting independently:

  PUBLIC_API_CONTRACT.md  ←→  platform_layer/__init__.py  ←→  filesystem
        (document)                  (code record)               (reality)

If you update the contract doc but not the __init__.py, test 1 fails.
If you update the __init__.py but not the contract doc, test 1 fails.
If __all__ drifts from PUBLIC_MODULES, test 2 fails.
If a module is listed but the file doesn't exist, test 3 fails.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.runtime_only


def _contract_platform_layer_modules() -> set[str]:
    """Parse all AINDY.platform_layer.* lines from PUBLIC_API_CONTRACT.md."""
    contract_path = ROOT / "docs" / "runtime" / "PUBLIC_API_CONTRACT.md"
    text = contract_path.read_text(encoding="utf-8")
    modules: set[str] = set()
    in_public_section = False
    for line in text.splitlines():
        if line.strip() == "## Public Runtime API Modules":
            in_public_section = True
            continue
        if in_public_section and line.startswith("## "):
            break
        if in_public_section:
            candidate = line.lstrip("- ").strip().strip("`")
            if candidate.startswith("AINDY.platform_layer."):
                modules.add(candidate)
    return modules


# ── Test 1: __init__.py PUBLIC_MODULES matches the contract document ──────────

def test_public_modules_matches_contract_document():
    from AINDY.platform_layer import PUBLIC_MODULES

    contract_modules = _contract_platform_layer_modules()
    assert contract_modules, "No AINDY.platform_layer.* entries found in PUBLIC_API_CONTRACT.md — check parsing"

    extra_in_code = PUBLIC_MODULES - contract_modules
    extra_in_doc = contract_modules - PUBLIC_MODULES

    assert not extra_in_code, (
        f"Modules in platform_layer/__init__.py PUBLIC_MODULES but not in "
        f"PUBLIC_API_CONTRACT.md: {sorted(extra_in_code)}"
    )
    assert not extra_in_doc, (
        f"Modules in PUBLIC_API_CONTRACT.md but not in "
        f"platform_layer/__init__.py PUBLIC_MODULES: {sorted(extra_in_doc)}"
    )


# ── Test 2: __all__ derives from PUBLIC_MODULES, no independent drift ─────────

def test_all_derives_from_public_modules():
    from AINDY.platform_layer import PUBLIC_MODULES
    import AINDY.platform_layer as pl

    all_as_qualified = frozenset(f"AINDY.platform_layer.{name}" for name in pl.__all__)

    extra_in_all = all_as_qualified - PUBLIC_MODULES
    extra_in_public = PUBLIC_MODULES - all_as_qualified

    assert not extra_in_all, (
        f"Names in platform_layer/__all__ not in PUBLIC_MODULES: "
        f"{sorted(n.split('.')[-1] for n in extra_in_all)}"
    )
    assert not extra_in_public, (
        f"Names in PUBLIC_MODULES not in platform_layer/__all__: "
        f"{sorted(n.split('.')[-1] for n in extra_in_public)}"
    )


# ── Test 3: every declared public module exists on disk ───────────────────────

def test_all_public_modules_exist_as_files():
    from AINDY.platform_layer import PUBLIC_MODULES

    missing = []
    for qualified in sorted(PUBLIC_MODULES):
        file_path = ROOT / (qualified.replace(".", "/") + ".py")
        if not file_path.is_file():
            missing.append(qualified)

    assert not missing, (
        f"Public modules declared in platform_layer/__init__.py "
        f"have no corresponding .py file: {missing}"
    )
