from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_BLOCKED_ROOT_KEYS = {
    "db",
    "_db",
    "session",
    "engine",
    "settings",
    "config",
    "secret",
    "secrets",
    "request",
    "response",
    "app",
}


def sanitize_extension_payload(value: Any) -> Any:
    return _sanitize(value, root=False)


def sanitize_extension_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    return _sanitize_mapping(context or {}, root=True)


def _sanitize(value: Any, *, root: bool) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, root=root)
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, root=False) for item in value]
    if _looks_like_sqlalchemy_session(value) or _looks_like_orm_object(value):
        return {"_redacted_type": type(value).__name__}
    if _looks_like_internal_runtime_object(value):
        return {"_redacted_type": type(value).__name__}
    return {"_redacted_type": type(value).__name__}


def _sanitize_mapping(value: Mapping[str, Any], *, root: bool) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if root and key in _BLOCKED_ROOT_KEYS:
            continue
        sanitized[key] = _sanitize(raw_value, root=False)
    return sanitized


def _looks_like_sqlalchemy_session(value: Any) -> bool:
    cls = value.__class__
    return cls.__name__ in {"Session", "AsyncSession"} and cls.__module__.startswith(
        "sqlalchemy.orm"
    )


def _looks_like_orm_object(value: Any) -> bool:
    cls = value.__class__
    module_name = str(getattr(cls, "__module__", ""))
    if module_name.startswith("AINDY.db.models"):
        return True
    state = getattr(value, "_sa_instance_state", None)
    return state is not None


def _looks_like_internal_runtime_object(value: Any) -> bool:
    cls = value.__class__
    module_name = str(getattr(cls, "__module__", ""))
    return module_name.startswith("AINDY.")
