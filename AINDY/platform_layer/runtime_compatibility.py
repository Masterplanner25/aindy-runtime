from __future__ import annotations

import logging
from typing import Any, Iterable

from AINDY._version import __version__ as RUNTIME_PACKAGE_VERSION
from AINDY.config import settings

logger = logging.getLogger(__name__)

RUNTIME_PACKAGE_NAME = "aindy-runtime"
COMPATIBILITY_DECLARATION_FORMAT = "pep440"

# DEBT-COMPAT-1 — what the last `load_plugins` found each plugin's distribution declaring about
# this runtime. Replaced, never appended, on every check.
_consumer_checks: list[dict[str, Any]] = []

_UPPER_BOUND_OPERATORS = frozenset({"<", "<=", "==", "===", "~="})


def _major_series(version: str) -> str:
    parts = (version or "0.0.0").split(".")
    major = parts[0] if parts and parts[0].isdigit() else "0"
    return f">={major}.0,<{int(major) + 1}.0"


def runtime_repo_compatibility_metadata() -> dict[str, object]:
    runtime_version = RUNTIME_PACKAGE_VERSION
    api_version = settings.API_VERSION
    return {
        "runtime_package": {
            "name": RUNTIME_PACKAGE_NAME,
            "version": runtime_version,
        },
        "apps_repo_contract": {
            "declaration_format": COMPATIBILITY_DECLARATION_FORMAT,
            "recommended_runtime_requirement": _major_series(runtime_version),
            "compatible_runtime_major": runtime_version.split(".")[0],
            "compatible_api_major": api_version.split(".")[0],
            "policy": (
                "The apps repo must declare a normal Python dependency range on "
                "aindy-runtime with an explicit upper bound before the next MAJOR "
                "runtime release. Runtime package MAJOR and API MAJOR indicate "
                "repo-split compatibility boundaries."
            ),
        },
        "consumers": consumer_requirement_checks(),
    }


def consumer_requirement_checks() -> list[dict[str, Any]]:
    """The last plugin load's consumer checks (DEBT-COMPAT-1), served on `/api/version`."""
    return [dict(check) for check in _consumer_checks]


def _canonical(name: str) -> str:
    from packaging.utils import canonicalize_name

    return str(canonicalize_name(name))


def _runtime_location() -> str:
    # Every version instrument is cwd-sensitive; say where this answer came from (DEBT-COMPAT-1).
    import AINDY

    return ", ".join(str(p) for p in getattr(AINDY, "__path__", []))


def _declared_runtime_requirement(requires: Iterable[str] | None):
    """The distribution's combined ``aindy-runtime`` specifier, or None if it declares none.

    A requirement under an extra (``; extra == "x"``) is not what a plain install enforces, so it
    does not count.
    """
    from packaging.requirements import InvalidRequirement, Requirement
    combined = None
    for raw in requires or ():
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            continue
        if _canonical(req.name) != RUNTIME_PACKAGE_NAME:
            continue
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        combined = req.specifier if combined is None else combined & req.specifier
    return combined


def check_consumer_requirements(
    module_names: Iterable[str],
    *,
    runtime_version: str = RUNTIME_PACKAGE_VERSION,
    packages_distributions=None,
    distribution=None,
) -> list[dict[str, Any]]:
    """DEBT-COMPAT-1 — compare each plugin's declared ``aindy-runtime`` range with this runtime.

    The runtime already published its policy on `/api/version` ("declare a range with an upper
    bound") and nothing read it: a consumer sat a major behind, below the advertised floor, and a
    dev venv ran five releases older than the app's own declared range, with every suite green.
    Nothing fetches `/api/version`. The one place that sees both the running runtime and the
    consumer's declaration is plugin load, so the comparison is made here, from the installed
    metadata of the distribution that owns each plugin module.

    WARNS, never refuses (DEC-080): a hard gate on a version check is how a working deployment
    dies on a patch bump. A module no installed distribution owns (run from a source tree that
    was never installed) is reported ``not_installed`` at INFO: nothing was declared to compare.
    Never raises.
    """
    import importlib.metadata as md

    from packaging.version import Version

    packages_distributions = packages_distributions or md.packages_distributions
    distribution = distribution or md.distribution

    results: list[dict[str, Any]] = []
    try:
        owners = packages_distributions()
    except Exception as exc:  # noqa: BLE001 — a compatibility probe must never fail a boot
        logger.debug("DEBT-COMPAT-1: could not map packages to distributions: %s", exc)
        owners = {}

    by_dist: dict[str | None, list[str]] = {}
    for module_name in module_names:
        top = str(module_name).split(".")[0]
        if top == "AINDY":
            continue  # the runtime's own built-ins are not a consumer of it
        owned_by = sorted(set(owners.get(top) or []))
        if owned_by and all(_canonical(d) == RUNTIME_PACKAGE_NAME for d in owned_by):
            continue
        dists = [d for d in owned_by if _canonical(d) != RUNTIME_PACKAGE_NAME] or [None]
        for dist_name in dists:
            by_dist.setdefault(dist_name, []).append(str(module_name))

    for dist_name, modules in by_dist.items():
        record: dict[str, Any] = {
            "distribution": dist_name,
            "distribution_version": None,
            "modules": sorted(set(modules)),
            "requirement": None,
            "upper_bound": None,
            "runtime_version": runtime_version,
            "status": "not_installed",
        }
        if dist_name is None:
            results.append(record)
            continue
        try:
            dist = distribution(dist_name)
            record["distribution_version"] = dist.version
            spec = _declared_runtime_requirement(dist.requires)
            if spec is None:
                record["status"] = "undeclared"
            else:
                record["requirement"] = str(spec)
                record["upper_bound"] = any(s.operator in _UPPER_BOUND_OPERATORS for s in spec)
                record["status"] = (
                    "satisfied" if spec.contains(Version(runtime_version), prereleases=True) else "unsatisfied"
                )
        except Exception as exc:  # noqa: BLE001 — unreadable metadata is `unknown`, never a boot failure
            record["status"] = "unknown"
            record["error"] = f"{type(exc).__name__}: {exc}"
        results.append(record)

    _consumer_checks[:] = results
    for record in results:
        _log_consumer_check(record)
    return results


def _log_consumer_check(record: dict[str, Any]) -> None:
    dist = record["distribution"]
    runtime = f"{record['runtime_version']} (from {_runtime_location()})"
    status = record["status"]
    if status == "unsatisfied":
        logger.warning(
            "DEBT-COMPAT-1: plugin distribution %s %s declares %s %s, but the running runtime is %s. "
            "Boot continues; this is a warning, not a refusal.",
            dist, record["distribution_version"], RUNTIME_PACKAGE_NAME, record["requirement"], runtime,
        )
    elif status == "undeclared":
        logger.warning(
            "DEBT-COMPAT-1: plugin distribution %s %s loads into %s %s but declares no dependency on it, "
            "so no install step can notice when the two drift. Declare %s%s.",
            dist, record["distribution_version"], RUNTIME_PACKAGE_NAME, runtime,
            RUNTIME_PACKAGE_NAME, _major_series(record["runtime_version"]),
        )
    elif status == "satisfied" and not record["upper_bound"]:
        logger.warning(
            "DEBT-COMPAT-1: plugin distribution %s declares %s %s with no upper bound; the next MAJOR "
            "runtime release would install without complaint. Declare %s%s.",
            dist, RUNTIME_PACKAGE_NAME, record["requirement"],
            RUNTIME_PACKAGE_NAME, _major_series(record["runtime_version"]),
        )
    elif status == "not_installed":
        logger.info(
            "DEBT-COMPAT-1: plugin modules %s belong to no installed distribution; no declared "
            "%s range to compare with %s.",
            record["modules"], RUNTIME_PACKAGE_NAME, runtime,
        )
    elif status == "unknown":
        logger.info("DEBT-COMPAT-1: could not read %s's declared requirements: %s", dist, record.get("error"))
