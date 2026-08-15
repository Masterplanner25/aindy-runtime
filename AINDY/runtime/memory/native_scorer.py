from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Sequence

logger = logging.getLogger(__name__)

_bridge = None
_load_attempted = False
_stats = {
    "calls": 0,
    "fallbacks": 0,
    "errors": 0,
    "last_duration_ms": 0.0,
    "native_duration_ms": 0.0,
}


def score_memory_nodes(
    *,
    similarities: Sequence[float],
    recencies: Sequence[float],
    success_rates: Sequence[float],
    usage_frequencies: Sequence[float],
    graph_bonuses: Sequence[float],
    impact_scores: Sequence[float],
    trace_bonuses: Sequence[float],
    low_value_flags: Sequence[bool],
) -> dict[str, Any]:
    started = time.perf_counter()
    _stats["calls"] += 1

    if not _native_enabled():
        return _fallback_result(
            reason="disabled",
            started=started,
        )

    bridge = _load_bridge()
    if bridge is None:
        return _fallback_result(
            reason="unavailable",
            started=started,
        )

    try:
        scores = list(
            bridge.score_memory_nodes(
                list(similarities),
                list(recencies),
                list(success_rates),
                list(usage_frequencies),
                list(graph_bonuses),
                list(impact_scores),
                list(trace_bonuses),
                list(low_value_flags),
            )
        )
        duration_ms = _elapsed_ms(started)
        _stats["last_duration_ms"] = duration_ms
        _stats["native_duration_ms"] = duration_ms
        return {
            "scores": scores,
            "engine": "native",
            "duration_ms": duration_ms,
            "fallback_used": False,
            "error": None,
        }
    except Exception as exc:
        logger.warning("[MemoryNativeScorer] native scoring failed, falling back to Python: %s", exc)
        _stats["errors"] += 1
        return _fallback_result(
            reason="runtime_error",
            started=started,
            error=str(exc),
        )


def get_native_scorer_stats() -> dict[str, float | int]:
    calls = int(_stats["calls"] or 0)
    errors = int(_stats["errors"] or 0)
    fallbacks = int(_stats["fallbacks"] or 0)
    return {
        "calls": calls,
        "fallbacks": fallbacks,
        "errors": errors,
        "fallback_rate": (fallbacks / calls) if calls else 0.0,
        "error_rate": (errors / calls) if calls else 0.0,
        "last_duration_ms": float(_stats["last_duration_ms"] or 0.0),
        "native_duration_ms": float(_stats["native_duration_ms"] or 0.0),
    }


def _fallback_result(*, reason: str, started: float, error: str | None = None) -> dict[str, Any]:
    _stats["fallbacks"] += 1
    duration_ms = _elapsed_ms(started)
    _stats["last_duration_ms"] = duration_ms
    if error:
        logger.info("[MemoryNativeScorer] fallback engaged (%s): %s", reason, error)
    else:
        logger.info("[MemoryNativeScorer] fallback engaged (%s)", reason)
    return {
        "scores": None,
        "engine": "python",
        "duration_ms": duration_ms,
        "fallback_used": True,
        "error": error or reason,
    }


def _native_enabled() -> bool:
    value = os.getenv("USE_NATIVE_SCORER", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _load_bridge():
    """Return the compiled extension, or None.

    NATIVE-DISCOVERY-1: the search policy used to live here *and*, differently, in
    `memory/embedding_service.py` (debug-only), so the two disagreed about whether the
    native path was available in the same process. Both now delegate to
    `AINDY.memory.native_bridge`, which owns the profile order and the once-per-process
    cache. The module-level `_bridge` / `_load_attempted` here mirror that cache so the
    stats and fallback bookkeeping below keep their existing meaning.
    """
    global _bridge, _load_attempted

    if _bridge is not None:
        return _bridge
    if _load_attempted:
        return None
    _load_attempted = True

    from AINDY.memory.native_bridge import load_bridge as _load_shared

    _bridge = _load_shared()
    if _bridge is None:
        logger.info("[MemoryNativeScorer] native bridge unavailable")
    return _bridge


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 4)
