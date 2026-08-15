"""Behavioural suite for the native memory scorer and its Python fallback.

Closes the native-scorer half of DOCS-COVERAGE-CLAIM-1, which recorded that
`NATIVE_MEMORY_BRIDGE.md` cited `tests/integration/test_memory_native_scorer.py`
and `tests/integration/test_memory_bridge.py` — neither had ever existed — and that
nothing under `tests/` referenced `memory_bridge_rs` at all. Agrees with NATIVE-CI-1:
the crate has no Rust tests either, so this was uncovered in both languages.

Layering under test:

    scorer._score_nodes           picks an engine, falls back on any failure
      └─ native_scorer.score_memory_nodes    envelope + stats + load caching
           └─ memory_bridge_rs (pyo3)        Rust
                └─ memory_cpp/semantic.cpp   C++ kernel

Most tests here need no native build — they cover the envelope, the fallback
routing and the Python formula. The parity tests skip when the extension is not
importable, which is the normal case in `Runtime Contracts` (CI builds the crate
in a *separate* job, `Native Crate Build (Rust)`, and never imports it).

**Building it locally** (Windows): `cargo build` in
`AINDY/memory/native/memory_bridge_rs` emits `memory_bridge_rs.dll`; Python will
not import that. Copy it to `memory_bridge_rs.pyd` in the same directory and the
parity tests activate.
"""
from __future__ import annotations

import importlib
import math
import os
import sys

import pytest

from AINDY.runtime.memory import native_scorer
from AINDY.runtime.memory.scorer import _normalize_usage, _score_node_python, _score_nodes

pytestmark = pytest.mark.runtime_only


# ── native module discovery ───────────────────────────────────────────────────


def _try_import_bridge():
    """Import the compiled extension if a local build made one importable.

    Anchored on the `AINDY.memory` package rather than on a chain of `..` hops,
    so it cannot silently drift out of sync with the crate's real location.
    """
    import AINDY.memory

    root = os.path.join(
        os.path.dirname(os.path.abspath(AINDY.memory.__file__)),
        "native",
        "memory_bridge_rs",
        "target",
    )
    for profile in ("release", "debug"):
        candidate = os.path.join(root, profile)
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)
    try:
        return importlib.import_module("memory_bridge_rs")
    except Exception:
        return None


BRIDGE = _try_import_bridge()
requires_native = pytest.mark.skipif(
    BRIDGE is None,
    reason="memory_bridge_rs not importable — build the crate and copy .dll to .pyd",
)


@pytest.fixture
def clean_scorer_state():
    """Isolate native_scorer's module globals.

    `_bridge`, `_load_attempted` and `_stats` are module-level and sticky; without
    this every test would inherit the previous one's load outcome and counters.
    """
    saved = (native_scorer._bridge, native_scorer._load_attempted, dict(native_scorer._stats))
    native_scorer._bridge = None
    native_scorer._load_attempted = False
    for key in native_scorer._stats:
        native_scorer._stats[key] = 0 if isinstance(native_scorer._stats[key], int) else 0.0
    yield
    native_scorer._bridge, native_scorer._load_attempted = saved[0], saved[1]
    native_scorer._stats.update(saved[2])


def _vectors(n=1, **overrides):
    base = {
        "similarities": [0.5] * n,
        "recencies": [0.5] * n,
        "success_rates": [0.5] * n,
        "usage_frequencies": [3.0] * n,
        "graph_bonuses": [0.1] * n,
        "impact_scores": [0.0] * n,
        "trace_bonuses": [0.0] * n,
        "low_value_flags": [False] * n,
    }
    base.update(overrides)
    return base


def _prepared(**overrides):
    node = {
        "similarity": 0.5,
        "recency": 0.5,
        "success_rate": 0.5,
        "usage_frequency": 3.0,
        "graph_bonus": 0.1,
        "impact_score": 0.0,
        "trace_bonus": 0.0,
        "low_value_flag": False,
    }
    node.update(overrides)
    return node


# ── envelope + engine selection ───────────────────────────────────────────────


class TestScorerEnvelope:
    def test_disabled_by_env_returns_python_engine(self, clean_scorer_state, monkeypatch):
        monkeypatch.setenv("USE_NATIVE_SCORER", "0")
        result = native_scorer.score_memory_nodes(**_vectors())
        assert result["engine"] == "python"
        assert result["fallback_used"] is True
        assert result["error"] == "disabled"
        assert result["scores"] is None

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", " Off "])
    def test_all_falsey_spellings_disable(self, clean_scorer_state, monkeypatch, value):
        monkeypatch.setenv("USE_NATIVE_SCORER", value)
        assert native_scorer._native_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything-else", ""])
    def test_everything_else_enables(self, clean_scorer_state, monkeypatch, value):
        monkeypatch.setenv("USE_NATIVE_SCORER", value)
        assert native_scorer._native_enabled() is True

    def test_default_is_enabled_when_unset(self, clean_scorer_state, monkeypatch):
        monkeypatch.delenv("USE_NATIVE_SCORER", raising=False)
        assert native_scorer._native_enabled() is True

    def test_unavailable_bridge_reports_unavailable(self, clean_scorer_state, monkeypatch):
        monkeypatch.setenv("USE_NATIVE_SCORER", "1")
        monkeypatch.setattr(native_scorer, "_load_bridge", lambda: None)
        result = native_scorer.score_memory_nodes(**_vectors())
        assert (result["engine"], result["error"]) == ("python", "unavailable")

    def test_bridge_exception_reports_runtime_error_and_does_not_propagate(
        self, clean_scorer_state, monkeypatch
    ):
        class Boom:
            def score_memory_nodes(self, *args):
                raise RuntimeError("kernel exploded")

        monkeypatch.setenv("USE_NATIVE_SCORER", "1")
        monkeypatch.setattr(native_scorer, "_load_bridge", lambda: Boom())
        result = native_scorer.score_memory_nodes(**_vectors())
        assert result["error"] == "kernel exploded"
        assert result["fallback_used"] is True
        assert result["scores"] is None

    def test_successful_native_call_reports_native_engine(self, clean_scorer_state, monkeypatch):
        class Fake:
            def score_memory_nodes(self, *args):
                return [0.75]

        monkeypatch.setenv("USE_NATIVE_SCORER", "1")
        monkeypatch.setattr(native_scorer, "_load_bridge", lambda: Fake())
        result = native_scorer.score_memory_nodes(**_vectors())
        assert result == {
            "scores": [0.75],
            "engine": "native",
            "duration_ms": result["duration_ms"],
            "fallback_used": False,
            "error": None,
        }

    def test_envelope_keys_are_identical_across_every_outcome(
        self, clean_scorer_state, monkeypatch
    ):
        """Callers branch on `scores is None`; the key set must never vary."""
        expected = {"scores", "engine", "duration_ms", "fallback_used", "error"}

        monkeypatch.setenv("USE_NATIVE_SCORER", "0")
        assert set(native_scorer.score_memory_nodes(**_vectors())) == expected

        monkeypatch.setenv("USE_NATIVE_SCORER", "1")
        monkeypatch.setattr(native_scorer, "_load_bridge", lambda: None)
        assert set(native_scorer.score_memory_nodes(**_vectors())) == expected


# ── stats accounting ──────────────────────────────────────────────────────────


class TestStats:
    def test_zero_calls_yields_zero_rates_not_division_error(self, clean_scorer_state):
        stats = native_scorer.get_native_scorer_stats()
        assert stats["fallback_rate"] == 0.0
        assert stats["error_rate"] == 0.0

    def test_fallbacks_and_errors_are_counted_separately(self, clean_scorer_state, monkeypatch):
        class Boom:
            def score_memory_nodes(self, *args):
                raise RuntimeError("nope")

        monkeypatch.setenv("USE_NATIVE_SCORER", "1")
        monkeypatch.setattr(native_scorer, "_load_bridge", lambda: Boom())
        native_scorer.score_memory_nodes(**_vectors())

        stats = native_scorer.get_native_scorer_stats()
        assert stats["calls"] == 1
        assert stats["errors"] == 1
        assert stats["fallbacks"] == 1
        assert stats["fallback_rate"] == 1.0

    def test_a_disabled_call_is_a_fallback_but_not_an_error(
        self, clean_scorer_state, monkeypatch
    ):
        monkeypatch.setenv("USE_NATIVE_SCORER", "0")
        native_scorer.score_memory_nodes(**_vectors())
        stats = native_scorer.get_native_scorer_stats()
        assert (stats["fallbacks"], stats["errors"]) == (1, 0)


class TestLoadCaching:
    def test_a_prior_failed_attempt_latches_off_even_if_the_module_is_importable(
        self, clean_scorer_state
    ):
        """`_load_attempted` latches, so one failed import disables the native path
        for the life of the process.

        Asserted without touching import machinery: mark the attempt as already
        made and confirm `_load_bridge` short-circuits to None *even when the
        extension would import fine*. Same shape as the `ResourceManager._get_backend()`
        cache noted in CLAUDE.md — fixing the environment later has no effect
        without a restart.
        """
        native_scorer._bridge = None
        native_scorer._load_attempted = True

        assert native_scorer._load_bridge() is None
        assert native_scorer._bridge is None

    def test_an_already_loaded_bridge_is_returned_without_reimporting(
        self, clean_scorer_state
    ):
        sentinel = object()
        native_scorer._bridge = sentinel
        native_scorer._load_attempted = True

        assert native_scorer._load_bridge() is sentinel

    def test_load_sets_the_latch_on_the_first_call(self, clean_scorer_state):
        assert native_scorer._load_attempted is False
        native_scorer._load_bridge()
        assert native_scorer._load_attempted is True


# ── the Python formula ────────────────────────────────────────────────────────


class TestPythonFormula:
    def test_all_zero_inputs_score_zero(self):
        node = _prepared(
            similarity=0.0, recency=0.0, success_rate=0.0,
            usage_frequency=0.0, graph_bonus=0.0, impact_score=0.0,
        )
        assert _score_node_python(node) == 0.0

    def test_similarity_dominates_at_weight_040(self):
        low = _score_node_python(_prepared(similarity=0.0))
        high = _score_node_python(_prepared(similarity=1.0))
        assert high - low == pytest.approx(0.40)

    def test_success_weight_steps_up_above_usage_five(self):
        """0.20 at or below 5 uses, 0.25 above — a step, not a ramp."""
        at_five = _prepared(usage_frequency=5.0, success_rate=1.0)
        above = _prepared(usage_frequency=5.001, success_rate=1.0)
        delta = _score_node_python(above) - _score_node_python(at_five)
        usage_delta = 0.10 * (_normalize_usage(5.001) - _normalize_usage(5.0))
        assert delta - usage_delta == pytest.approx(0.05, abs=1e-9)

    def test_low_value_flag_halves_the_score(self):
        normal = _score_node_python(_prepared())
        flagged = _score_node_python(_prepared(low_value_flag=True))
        assert flagged == pytest.approx(normal * 0.5)

    def test_impact_bonus_saturates_at_five(self):
        assert _score_node_python(_prepared(impact_score=5.0)) == pytest.approx(
            _score_node_python(_prepared(impact_score=500.0))
        )

    def test_trace_bonus_is_added_verbatim(self):
        delta = _score_node_python(_prepared(trace_bonus=0.10)) - _score_node_python(_prepared())
        assert delta == pytest.approx(0.10)

    @pytest.mark.parametrize("usage", [-5.0, 0.0])
    def test_normalize_usage_floors_at_zero(self, usage):
        assert _normalize_usage(usage) == 0.0

    def test_normalize_usage_reaches_one_at_hundred(self):
        assert _normalize_usage(100.0) == pytest.approx(1.0)

    def test_normalize_usage_is_monotonic(self):
        values = [_normalize_usage(v) for v in (0.5, 1, 5, 20, 60, 100)]
        assert values == sorted(values)


# ── engine routing in scorer._score_nodes ─────────────────────────────────────


class TestEngineRouting:
    def test_python_scores_used_when_native_returns_none(self, clean_scorer_state, monkeypatch):
        monkeypatch.setenv("USE_NATIVE_SCORER", "0")
        nodes = [_prepared(similarity=0.9), _prepared(similarity=0.1)]
        scores = _score_nodes(nodes)
        assert scores == [_score_node_python(nodes[0]), _score_node_python(nodes[1])]

    def test_native_scores_used_verbatim_when_present(self, clean_scorer_state, monkeypatch):
        monkeypatch.setattr(
            "AINDY.runtime.memory.scorer.score_memory_nodes_native",
            lambda **kwargs: {"scores": [1.0, 2.0], "error": None},
        )
        assert _score_nodes([_prepared(), _prepared()]) == [1.0, 2.0]

    def test_empty_input_produces_empty_output(self, clean_scorer_state, monkeypatch):
        monkeypatch.setenv("USE_NATIVE_SCORER", "0")
        assert _score_nodes([]) == []


# ── native kernel contract (skipped without a build) ──────────────────────────


@requires_native
class TestNativeSimilarityContract:
    def test_identical_vectors_score_one(self):
        assert BRIDGE.semantic_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self):
        assert BRIDGE.semantic_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_score_minus_one(self):
        assert BRIDGE.semantic_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_magnitude_is_ignored_only_direction_counts(self):
        assert BRIDGE.semantic_similarity([1.0, 1.0], [50.0, 50.0]) == pytest.approx(1.0)

    def test_zero_vector_returns_zero_not_nan(self):
        """The C++ kernel guards `denom < 1e-15` — without it this divides by zero."""
        value = BRIDGE.semantic_similarity([0.0, 0.0], [1.0, 1.0])
        assert value == 0.0
        assert not math.isnan(value)

    def test_empty_vectors_return_zero(self):
        assert BRIDGE.semantic_similarity([], []) == 0.0

    def test_length_mismatch_raises_valueerror(self):
        with pytest.raises(ValueError, match="same length"):
            BRIDGE.semantic_similarity([1.0, 2.0], [1.0])

    def test_result_stays_within_minus_one_and_one(self):
        for a, b in ([[3.0, 1.0], [1.0, 3.0]], [[-2.0, 5.0], [4.0, -1.0]]):
            assert -1.0 <= BRIDGE.semantic_similarity(a, b) <= 1.0


@requires_native
class TestNativeWeightedDotContract:
    def test_matches_the_documented_engagement_formula(self):
        values = [10.0, 5.0, 4.0, 3.0, 2.0]
        weights = [2.0, 3.0, 1.5, 1.0, 0.5]
        assert BRIDGE.weighted_dot_product(values, weights) == pytest.approx(
            10 * 2 + 5 * 3 + 4 * 1.5 + 3 * 1 + 2 * 0.5
        )

    def test_empty_returns_zero(self):
        assert BRIDGE.weighted_dot_product([], []) == 0.0

    def test_length_mismatch_raises_valueerror(self):
        with pytest.raises(ValueError, match="same length"):
            BRIDGE.weighted_dot_product([1.0], [1.0, 2.0])


@requires_native
class TestNativeScoreVectorContract:
    def test_ragged_vectors_raise_valueerror(self):
        with pytest.raises(ValueError, match="same length"):
            BRIDGE.score_memory_nodes(
                [0.5, 0.5], [0.5], [0.5], [3.0], [0.1], [0.0], [0.0], [False]
            )

    def test_empty_input_returns_empty_scores(self):
        assert BRIDGE.score_memory_nodes([], [], [], [], [], [], [], []) == []

    def test_low_value_flag_halves_like_the_python_side(self):
        args = ([0.5], [0.5], [0.5], [3.0], [0.1], [0.0], [0.0])
        normal = BRIDGE.score_memory_nodes(*args, [False])[0]
        flagged = BRIDGE.score_memory_nodes(*args, [True])[0]
        assert flagged == pytest.approx(normal * 0.5)


# ── extension discovery ───────────────────────────────────────────────────────


class TestExtensionDiscovery:
    """The two consumers of the crate do not look in the same places.

    `native_scorer._load_bridge` searches `target/release` then `target/debug`.
    `embedding_service.cosine_similarity` searches only `target/debug` — so in a
    release-built environment (what `Native Crate Build (Rust)` produces, and what
    any real deployment would produce) the cosine kernel is silently unavailable to
    the recall fallback path while the scorer uses it. One process, two different
    answers about whether native is available.
    """

    def test_native_scorer_searches_both_profiles(self):
        import inspect

        source = inspect.getsource(native_scorer._load_bridge)
        assert '"release"' in source and '"debug"' in source

    def test_embedding_service_searches_debug_only(self):
        import inspect

        from AINDY.memory import embedding_service

        source = inspect.getsource(embedding_service.cosine_similarity)
        assert '"debug"' in source
        assert '"release"' not in source, (
            "embedding_service.cosine_similarity now looks in target/release too — "
            "NATIVE-DISCOVERY-1 may be fixed; update this test and the debt entry."
        )

    def test_cosine_similarity_falls_back_silently_and_never_raises(self):
        """Whatever the build state, the public helper must return a float."""
        from AINDY.memory.embedding_service import cosine_similarity

        assert isinstance(cosine_similarity([1.0, 0.0], [0.0, 1.0]), float)

    def test_cosine_similarity_swallows_length_mismatch_instead_of_raising(self):
        """The native kernel raises ValueError on ragged input; the wrapper's blanket
        `except Exception` catches it and the Python fallback returns 0.0. So a real
        programming error is indistinguishable from 'no similarity'."""
        from AINDY.memory.embedding_service import cosine_similarity

        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_python_fallback_matches_the_native_contract_on_the_basics(self):
        from AINDY.memory.embedding_service import cosine_similarity_python

        assert cosine_similarity_python([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)
        assert cosine_similarity_python([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert cosine_similarity_python([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
        assert cosine_similarity_python([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert cosine_similarity_python([], []) == 0.0

    @requires_native
    def test_both_cosine_implementations_agree_when_native_is_present(self):
        from AINDY.memory.embedding_service import cosine_similarity_python

        for a, b in (
            ([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]),
            ([0.1, -0.2, 0.9], [-0.4, 0.5, 0.2]),
            ([1.0, 1.0], [50.0, 50.0]),
        ):
            assert BRIDGE.semantic_similarity(a, b) == pytest.approx(
                cosine_similarity_python(a, b), abs=1e-12
            )


# ── native ↔ Python parity ────────────────────────────────────────────────────


@requires_native
class TestEngineParity:
    """The two engines must agree — which one runs depends only on whether the
    extension happens to be importable, so any divergence makes recall ranking
    depend on build state."""

    def _native_score(self, prepared):
        return BRIDGE.score_memory_nodes(
            [prepared["similarity"]],
            [prepared["recency"]],
            [prepared["success_rate"]],
            [prepared["usage_frequency"]],
            [prepared["graph_bonus"]],
            [prepared["impact_score"]],
            [prepared["trace_bonus"]],
            [prepared["low_value_flag"]],
        )[0]

    @pytest.mark.parametrize(
        "overrides",
        [
            {},
            {"similarity": 1.0, "recency": 1.0},
            {"usage_frequency": 0.0},
            {"usage_frequency": 5.0},
            {"usage_frequency": 5.001},
            {"usage_frequency": 100.0},
            {"impact_score": 2.5},
            {"impact_score": 5.0},
            {"impact_score": 50.0},
            {"low_value_flag": True},
            {"trace_bonus": 0.10},
            {"graph_bonus": 1.0},
        ],
        ids=lambda o: ",".join(f"{k}={v}" for k, v in o.items()) or "baseline",
    )
    def test_engines_agree_on_non_negative_inputs(self, overrides):
        node = _prepared(**overrides)
        assert self._native_score(node) == pytest.approx(_score_node_python(node), abs=1e-12)

    def test_engines_agree_across_a_batch(self):
        nodes = [_prepared(similarity=s / 10.0, usage_frequency=float(s)) for s in range(10)]
        native = BRIDGE.score_memory_nodes(
            [n["similarity"] for n in nodes],
            [n["recency"] for n in nodes],
            [n["success_rate"] for n in nodes],
            [n["usage_frequency"] for n in nodes],
            [n["graph_bonus"] for n in nodes],
            [n["impact_score"] for n in nodes],
            [n["trace_bonus"] for n in nodes],
            [n["low_value_flag"] for n in nodes],
        )
        assert native == pytest.approx([_score_node_python(n) for n in nodes], abs=1e-12)

    @pytest.mark.parametrize("impact", [-0.5, -1.0, -5.0, -10.0])
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "NATIVE-PARITY-1: the engines disagree for a negative impact_score. Rust "
            "clamps `(impact/5.0).clamp(0.0, 1.0)` (lib.rs:181); Python uses "
            "`min(1.0, impact/5.0)` (scorer.py:120) with no lower bound, so the impact "
            "term goes negative. Measured delta up to +0.300 on a ~0.420 score. Which "
            "engine runs depends only on whether the extension is importable."
        ),
    )
    def test_engines_must_agree_on_negative_impact(self, impact):
        node = _prepared(impact_score=impact)
        assert self._native_score(node) == pytest.approx(_score_node_python(node), abs=1e-12)

    def test_negative_impact_divergence_is_bounded_by_the_missing_clamp(self):
        """Pins the *size* of NATIVE-PARITY-1 so a partial fix cannot pass silently.

        Runs without the xfail so the measured gap stays asserted: the whole
        divergence is the un-clamped impact term, nothing else.
        """
        node = _prepared(impact_score=-10.0)
        expected_gap = -min(1.0, node["impact_score"] / 5.0) * 0.15
        assert self._native_score(node) - _score_node_python(node) == pytest.approx(
            expected_gap, abs=1e-12
        )
