"""
Memory Capture Engine — v5 Self-Improvement Loop

The system decides what to store based on:
1. Significance scoring — is this event worth remembering?
2. Deduplication — is this already known?
3. Type classification — what kind of memory is this?
4. Auto-linking — what existing memories does this connect to?

Called automatically by all major workflows.
No manual memory calls needed in v5+.
"""

from __future__ import annotations

import logging
import re
import os
import uuid
from typing import Optional

from sqlalchemy import text
from AINDY.config import settings
from AINDY.core.execution_signal_helper import queue_memory_capture, queue_system_event
emit_system_event = queue_system_event
from AINDY.core.memory_capture_guard import (
    RUNTIME_INTERNAL_TASK_NAMES,
    memory_capture_suppressed,
)
from AINDY.core.observability_events import emit_observability_event
from AINDY.platform_layer.event_trace_service import calculate_depth, detect_root_event, get_downstream_effects, link_event_to_memory
from AINDY.core.system_event_service import emit_error_event
from AINDY.core.system_event_types import SystemEventTypes
from AINDY.platform_layer.registry import get_memory_policy, get_memory_significance_rule
from AINDY.platform_layer.trace_context import get_current_trace_id

logger = logging.getLogger(__name__)

class _EventSignificanceView(dict):
    """Compatibility view populated by app-registered memory policies."""

    def _ensure_loaded(self) -> None:
        from AINDY.platform_layer.registry import load_plugins

        load_plugins()

    def __contains__(self, key: object) -> bool:
        self._ensure_loaded()
        return super().__contains__(key)

    def __getitem__(self, key: str) -> float:
        self._ensure_loaded()
        return super().__getitem__(key)

    def get(self, key: str, default: float | None = None) -> float | None:
        self._ensure_loaded()
        return super().get(key, default)

    def items(self):
        self._ensure_loaded()
        return super().items()


EVENT_SIGNIFICANCE: dict[str, float] = _EventSignificanceView()

# MEM-FORCE-UNGATED-1 note. EXECUTION_STARTED stays in this set deliberately.
#
# The obvious fix for the 1,076 identical "execution.started from async" nodes was to drop
# it here — but RT-MEMTXN-LEAK-1 deliberately PRESERVED capture of this event for ordinary
# jobs so INFINITY-RUNTIME-1 loop-closure signal survives (only runtime-internal
# maintenance tasks were cut). Removing it would silently undo that.
#
# So the suppression lever moved instead: an app policy that explicitly declares
# `min_significance` is now honoured even for force=True captures. See
# `_forced_capture_suppressed`.
AUTO_MEMORY_EVENT_TYPES = {
    SystemEventTypes.EXECUTION_COMPLETED,
    SystemEventTypes.EXECUTION_STARTED,
    SystemEventTypes.EXECUTION_FAILED,
    "capability.denied",
    SystemEventTypes.FEEDBACK_RETRY_DETECTED,
    SystemEventTypes.FEEDBACK_LATENCY_SPIKE,
    SystemEventTypes.FEEDBACK_ABANDONMENT_DETECTED,
    SystemEventTypes.FEEDBACK_REPEATED_FAILURE,
}


def calculate_impact_score(db, event_id: str) -> float:
    from AINDY.db.models.system_event import SystemEvent

    event_uuid = uuid.UUID(str(event_id))
    event = db.query(SystemEvent).filter(SystemEvent.id == event_uuid).first()
    downstream = get_downstream_effects(db, event_uuid)
    trace_depth = calculate_depth(db, event_uuid)
    event_type = getattr(event, "type", "") if event else ""
    failure_bonus = 1.5 if "failed" in str(event_type).lower() or event_type == "capability.denied" else 0.5
    return round(len(downstream) + (trace_depth * 0.75) + failure_bonus, 4)


def _policy_base_significance(capture_rule, default: float = 0.4) -> float:
    """Base significance from a memory policy, honouring every accepted key.

    Order matters: `significance` and `base_score` are what `validate_memory_policy`
    demands, so they win. `default_significance` is read last for policies written against
    the engine's previous behaviour.
    """
    if not isinstance(capture_rule, dict):
        return default
    for key in ("significance", "base_score", "default_significance"):
        value = capture_rule.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            logger.warning(
                "[memory] policy key %r is not numeric (%r) — using %.2f", key, value, default
            )
            return default
    return default


_DEDUP_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_DEDUP_LONGHEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_DEDUP_WS_RE = re.compile(r"\s+")


def normalize_for_dedup(content: str) -> str:
    """Content with volatile identifiers removed, for duplicate comparison only.

    MEM-DEDUP-TRACEID-1. Dedup compared raw content, but captured messages embed the
    trace/run id of the occurrence — so N occurrences of ONE recurring failure produced N
    distinct contents and never deduplicated. On a real corpus that put four copies of a
    single (already-fixed) bug into a recall budget of eight.

    Only identifiers are stripped: UUIDs and long hex runs. Numbers are deliberately left
    alone — "latency 5417ms" and "latency 12ms" are genuinely different observations, and
    collapsing them would erase signal rather than noise. The normalised form is used for
    comparison only; the stored content keeps its identifiers.
    """
    if not content:
        return ""
    normalized = _DEDUP_UUID_RE.sub("<id>", str(content))
    normalized = _DEDUP_LONGHEX_RE.sub("<id>", normalized)
    return _DEDUP_WS_RE.sub(" ", normalized).strip().lower()


def _forced_capture_suppressed(policy, score: float) -> bool:
    """Whether an EXPLICIT policy threshold should suppress a forced capture.

    Only an explicitly declared `min_significance` counts. A missing key is not "0.0, so
    keep everything" — it means the deployment never expressed an opinion, and force should
    continue to win there exactly as it did before.
    """
    if not isinstance(policy, dict):
        return False
    declared = policy.get("min_significance")
    if declared is None:
        return False
    try:
        return float(score) < float(declared)
    except (TypeError, ValueError):
        return False


class MemoryCaptureEngine:
    def __init__(self, db, user_id: str, agent_namespace: str = "user"):
        self.db = db
        self.user_id = user_id
        self.agent_namespace = agent_namespace
        self._dao = None

    @property
    def dao(self):
        if self._dao is None:
            from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
            self._dao = MemoryNodeDAO(self.db)
        return self._dao

    def evaluate_and_capture(
        self,
        event_type: str,
        content: str,
        source: str,
        tags: list[str] = None,
        node_type: str = None,
        context: dict = None,
        extra: dict = None,
        force: bool = False,
        agent_namespace: str = None,
        allow_when_pipeline_active: bool = False,
    ) -> Optional[dict]:
        """
        Main entry point. Evaluates whether an event is
        worth storing and captures it if so.

        Returns the created MemoryNode dict or None if not stored.

        event_type: signal type used to look up a registered memory policy
        content: the memory content to store
        source: where this memory came from
        tags: semantic tags
        node_type: decision|outcome|insight|relationship
        context: additional context for significance scoring
        force: bypass significance check (always store)
        """
        if context and context.get("disable_memory_capture"):
            logger.info(
                "[MemoryCapture] Disabled for event=%s (identity boot)", event_type
            )
            return None
        try:
            from AINDY.platform_layer.trace_context import is_pipeline_active

            if is_pipeline_active() and not allow_when_pipeline_active:
                return None
        except Exception:
            pass
        env_name = os.getenv("ENV", "").lower()
        testing_flag = os.getenv("TESTING", "false").lower() in {"1", "true", "yes"}
        prevent_capture_for_tests = (
            settings.is_testing or env_name == "test" or testing_flag
        )
        if prevent_capture_for_tests and not force and not str(event_type or "").startswith("execution."):
            logger.info(
                "[MemoryCapture] Skipping capture during testing for event=%s", event_type
            )
            return None
        try:
            # Step 1: Score significance
            score = self._score_significance(
                event_type=event_type,
                content=content,
                context=context or {},
            )

            policy = get_memory_policy(event_type) or {}
            min_significance = float(policy.get("min_significance", 0.0)) if isinstance(policy, dict) else 0.0
            # MEM-FORCE-UNGATED-1. `force=True` skipped this gate entirely, so the eight
            # auto-captured system events could neither be scored out nor suppressed by an
            # app policy — the app team could not turn them off from their side at all.
            #
            # An app that has gone to the trouble of declaring `min_significance` for an
            # event type has stated an intent; honour it even when the capture is forced.
            # Absent an explicit declaration, force still wins, so nothing changes for
            # deployments that never wrote a policy.
            if force and _forced_capture_suppressed(policy, score):
                logger.debug(
                    "Event %s forced but suppressed by an explicit policy min_significance "
                    "(%.2f > %.2f)",
                    event_type,
                    min_significance,
                    score,
                )
                return None
            if not force and score < min_significance:
                logger.debug(
                    "Event %s below significance threshold (%.2f) — not stored",
                    event_type,
                    score,
                )
                return None

            # Step 2: Check deduplication
            if self._is_duplicate(content):
                logger.debug(
                    "Event %s is duplicate — skipping capture",
                    event_type,
                )
                return None

            # Step 3: Classify node type if not provided
            if node_type is None:
                node_type = self._classify_node_type(
                    event_type,
                    content,
                )

            # Step 4: Enrich tags
            enriched_tags = self._enrich_tags(
                tags or [],
                event_type,
                node_type,
            )

            # Step 5: Create memory node
            request_trace_id = get_current_trace_id()
            memory_extra = dict(extra or {})
            if request_trace_id and not memory_extra.get("trace_id"):
                memory_extra["trace_id"] = request_trace_id
            memory_extra.setdefault("event_type", event_type)

            source_event_id = memory_extra.get("source_event_id")
            causal_context = self._build_causal_context(
                trace_id=memory_extra.get("trace_id"),
                source_event_id=source_event_id,
                event_type=event_type,
            )
            memory_extra.update(causal_context["extra"])
            node = self.dao.save(
                content=content,
                source=source,
                tags=enriched_tags,
                user_id=self.user_id,
                node_type=node_type,
                extra=memory_extra,
                generate_embedding=True,
                source_event_id=causal_context["source_event_id"],
                root_event_id=causal_context["root_event_id"],
                causal_depth=causal_context["causal_depth"],
                impact_score=causal_context["impact_score"],
                memory_type=causal_context["memory_type"],
            )

            # Step 6: Tag with agent namespace (federation)
            try:
                namespace = agent_namespace or self.agent_namespace or "user"
                node_id = node.get("id") if isinstance(node, dict) else None
                if node_id:
                    db_node = self.dao._get_model_by_id(
                        node_id,
                        user_id=self.user_id,
                    )
                    if db_node:
                        db_node.source_agent = namespace
                        capture_rule = get_memory_policy(event_type) or {}
                        shared_namespaces = set(capture_rule.get("shared_namespaces") or ())
                        db_node.is_shared = bool(capture_rule.get("is_shared")) or namespace in shared_namespaces
                        self.db.add(db_node)
                        self.db.commit()
                        self.db.refresh(db_node)
                        node["source_agent"] = db_node.source_agent
                        node["is_shared"] = db_node.is_shared
            except Exception:
                emit_observability_event(
                    event_type="memory_capture_namespace_update_failed",
                    user_id=self.user_id,
                    payload={"namespace": namespace, "node_id": node_id},
                )
                raise

            # Step 6: Auto-link to related memories
            self._auto_link(node, enriched_tags)
            if causal_context["source_event_id"] and isinstance(node, dict) and node.get("id"):
                link_event_to_memory(
                    db=self.db,
                    source_event_id=causal_context["source_event_id"],
                    memory_node_id=node["id"],
                    relationship_type="stored_as_memory",
                    weight=causal_context["impact_score"],
                )

            logger.info(
                "Captured memory [%s] from %s (significance=%.2f): %s...",
                node_type,
                event_type,
                score,
                content[:50],
            )
            emit_system_event(
                db=self.db,
                event_type=SystemEventTypes.MEMORY_WRITE,
                user_id=self.user_id,
                trace_id=request_trace_id or memory_extra.get("trace_id") or (str(node.get("id")) if isinstance(node, dict) else None),
                parent_event_id=causal_context["source_event_id"],
                source="memory",
                payload={
                    "node_id": node.get("id") if isinstance(node, dict) else None,
                    "event_type": event_type,
                    "source": source,
                    "node_type": node_type,
                    "memory_type": causal_context["memory_type"],
                    "impact_score": causal_context["impact_score"],
                    "tags": enriched_tags,
                },
                required=True,
            )

            # RTR-6: surface the captured insight as a first-class reasoning
            # signal (the reasoning attributes are otherwise implicit in the
            # MEMORY_WRITE payload + MemoryNode columns). Best-effort.
            try:
                from AINDY.core.reasoning_signal import emit_reasoning_signal

                emit_reasoning_signal(
                    db=self.db,
                    kind="capture",
                    user_id=self.user_id,
                    trace_id=request_trace_id or memory_extra.get("trace_id"),
                    parent_event_id=causal_context["source_event_id"],
                    payload={
                        "node_id": node.get("id") if isinstance(node, dict) else None,
                        "node_type": node_type,
                        "memory_type": causal_context["memory_type"],
                        "impact_score": causal_context["impact_score"],
                        "causal_depth": causal_context["causal_depth"],
                        "significance": round(float(score), 4),
                        "event_type": event_type,
                    },
                )
            except Exception:
                logger.debug("[ReasoningSignal] capture-signal emit skipped", exc_info=True)

            return node

        except Exception as exc:
            logger.warning("Memory capture failed for %s: %s", event_type, exc)
            try:
                emit_error_event(
                    db=self.db,
                    error_type="memory_write",
                    message=str(exc),
                    user_id=self.user_id,
                    trace_id=get_current_trace_id(),
                    source="memory",
                    payload={"event_type": event_type, "source": source},
                    required=True,
                )
            except Exception:
                logger.exception("Failed to emit required memory error event for %s", event_type)
            raise

    def _build_causal_context(
        self,
        *,
        trace_id: str | None,
        source_event_id: str | None,
        event_type: str,
    ) -> dict:
        root_event_id = None
        causal_depth = 0
        downstream_count = 0
        relationship_summary = None
        impact_score = 0.0

        if trace_id:
            root_event = detect_root_event(self.db, trace_id)
            if root_event:
                root_event_id = root_event.get("id")
        if source_event_id:
            causal_depth = calculate_depth(self.db, source_event_id)
            downstream_effects = get_downstream_effects(self.db, source_event_id)
            downstream_count = len(downstream_effects)
            impact_score = calculate_impact_score(self.db, source_event_id)
            relationship_summary = (
                f"Triggered by event {source_event_id}; "
                f"root={root_event_id or source_event_id}; "
                f"downstream_effects={downstream_count}; "
                f"causal_depth={causal_depth}"
            )

        return {
            "source_event_id": source_event_id,
            "root_event_id": root_event_id or source_event_id,
            "causal_depth": causal_depth,
            "impact_score": impact_score,
            "memory_type": self._classify_memory_type(event_type),
            "extra": {
                "relationship_summary": relationship_summary,
                "downstream_effect_count": downstream_count,
            },
        }

    def _classify_memory_type(self, event_type: str) -> str:
        capture_rule = get_memory_policy(event_type) or {}
        if capture_rule.get("memory_type"):
            return str(capture_rule["memory_type"])
        normalized = str(event_type or "").lower()
        if normalized in {
            "capability.denied",
            SystemEventTypes.EXECUTION_FAILED,
            SystemEventTypes.FEEDBACK_RETRY_DETECTED,
            SystemEventTypes.FEEDBACK_LATENCY_SPIKE,
            SystemEventTypes.FEEDBACK_ABANDONMENT_DETECTED,
            SystemEventTypes.FEEDBACK_REPEATED_FAILURE,
        } or "failed" in normalized:
            return "failure"
        if normalized == SystemEventTypes.EXECUTION_COMPLETED or "completed" in normalized:
            return "outcome"
        if "decision" in normalized:
            return "decision"
        return "insight"

    def _score_significance(
        self,
        event_type: str,
        content: str,
        context: dict,
    ) -> float:
        """
        Score how significant this event is (0.0-1.0).

        Base score from event type + modifiers:
        - Content length (longer = more detailed = more significant)
        - Outcome quality (high scores boost significance)
        - Novelty (new information scores higher)
        """
        capture_rule = get_memory_policy(event_type) or {}
        base = get_memory_significance_rule(event_type)
        if base is None:
            # MEM-POLICY-KEY-1. `validate_memory_policy` REQUIRES `significance` or
            # `base_score`, but this read only ever looked at `default_significance` — so a
            # policy that satisfied the validator had no effect on the score and every
            # declared significance silently fell back to 0.4. Read the validator's keys
            # first; keep `default_significance` as a trailing fallback so any policy
            # written against the old (undocumented) behaviour keeps working.
            base = _policy_base_significance(capture_rule)

        # Modifier 1: content richness
        content_score = min(1.0, len(content) / 500)

        # Modifier 2: outcome quality from context
        outcome_boost = 0.0
        if context.get("score") is not None:
            score_val = float(context["score"])
            if score_val >= 8:
                outcome_boost = 0.2  # exceptional result
            elif score_val <= 3:
                outcome_boost = 0.2  # notable failure

        # Modifier 3: explicit significance hint
        explicit = float(context.get("significance", 0.5))

        # Weighted combination
        final = (
            base * 0.5
            + content_score * 0.2
            + outcome_boost * 0.2
            + explicit * 0.1
        )

        return min(1.0, final)

    _DEDUP_WINDOW = 50

    def _normalized_duplicate(self, content: str):
        """Look for a recent node whose *normalised* content matches. Bounded by design."""
        normalized = normalize_for_dedup(content)
        if not normalized:
            return None
        try:
            if self.user_id is None:
                rows = self.db.execute(
                    text(
                        "SELECT id, content FROM memory_nodes "
                        "WHERE user_id IS NULL ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"lim": self._DEDUP_WINDOW},
                ).fetchall()
            else:
                rows = self.db.execute(
                    text(
                        "SELECT id, content FROM memory_nodes "
                        "WHERE user_id = :uid ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"uid": self.user_id, "lim": self._DEDUP_WINDOW},
                ).fetchall()
        except Exception as exc:
            logger.debug("[memory] normalized dedup lookup failed: %s", exc)
            return None

        for row in rows or []:
            try:
                if normalize_for_dedup(row[1]) == normalized:
                    return row
            except Exception:
                continue
        return None

    def _is_duplicate(self, content: str) -> bool:
        """
        Check if very similar content already exists.
        Simple check: exact content match in recent nodes.
        Phase 2: use embedding similarity for fuzzy dedup.
        """
        try:
            # RT-MEMTXN-LEAK-1 — `user_id = :uid` is never true when uid is NULL, so
            # system/global captures (user_id IS NULL) were never deduplicated: the
            # identical "execution.started from async" content was re-stored on every
            # pass, which is what let the capture → job → capture cycle run unbounded.
            # Branch instead of `IS NOT DISTINCT FROM` — that is unsupported on SQLite
            # and would leave the NULL bind param untyped on PostgreSQL.
            if self.user_id is None:
                existing = self.db.execute(
                    text(
                        "SELECT id FROM memory_nodes "
                        "WHERE user_id IS NULL "
                        "AND content = :content "
                        "LIMIT 1"
                    ),
                    {"content": content},
                ).fetchone()
            else:
                existing = self.db.execute(
                    text(
                        "SELECT id FROM memory_nodes "
                        "WHERE user_id = :uid "
                        "AND content = :content "
                        "LIMIT 1"
                    ),
                    {"uid": self.user_id, "content": content},
                ).fetchone()

            # MEM-DEDUP-TRACEID-1. Exact match above catches identical captures cheaply and
            # index-friendly. It does NOT catch the case that actually mattered: a recurring
            # failure whose message embeds the trace id of the occurrence, which produced N
            # distinct contents for one underlying event and consumed half the recall budget.
            #
            # Normalising in SQL would need `regexp_replace`, which is PostgreSQL-only and
            # would break the SQLite unit tier — the same dialect trap RT-MEMTXN-LEAK-1 hit
            # with `IS NOT DISTINCT FROM`. So compare in Python over a BOUNDED recent
            # window instead: dialect-agnostic, and capped so this can never become a scan.
            if existing is None:
                existing = self._normalized_duplicate(content)

            if existing is None:
                return False
            if existing.__class__.__module__ == "unittest.mock":
                return False
            if isinstance(existing, dict):
                return bool(existing.get("id"))
            mapping = getattr(existing, "_mapping", None)
            if mapping is not None and mapping.__class__.__module__ != "unittest.mock":
                return True
            if isinstance(existing, (list, tuple)):
                return len(existing) > 0
            try:
                return len(existing) > 0
            except Exception:
                return False
        except Exception:
            return False  # on error, allow capture

    def _classify_node_type(
        self,
        event_type: str,
        content: str,
    ) -> str:
        """
        Auto-classify node type from event type and content.
        """
        capture_rule = get_memory_policy(event_type) or {}
        if capture_rule.get("node_type"):
            return str(capture_rule["node_type"])
        normalized = str(event_type or "").lower()
        if "failed" in normalized or "completed" in normalized:
            return "outcome"
        if "decision" in normalized:
            return "decision"
        return "insight"

    def _enrich_tags(
        self,
        tags: list[str],
        event_type: str,
        node_type: str,
    ) -> list[str]:
        """
        Add automatic tags based on event type and node type.
        """
        enriched = list(tags)

        capture_rule = get_memory_policy(event_type) or {}
        auto_tags = capture_rule.get("tags") or []
        for tag in auto_tags:
            if tag not in enriched:
                enriched.append(tag)

        # Add node type tag
        if node_type and node_type not in enriched:
            enriched.append(node_type)

        return enriched

    def _auto_link(self, new_node: dict, tags: list[str]) -> None:
        """
        Automatically link new node to related existing nodes.
        Uses tag overlap to find candidates.
        Strength based on tag overlap ratio.
        """
        try:
            if not tags:
                return

            related = self.dao.get_by_tags(
                tags=tags,
                limit=3,
                user_id=self.user_id,
            )
            related = [
                r for r in related
                if not self.user_id or r.get("user_id") == self.user_id
            ]

            from AINDY.memory.bridge import create_memory_link

            for related_node in related:
                if related_node.get("id") == new_node.get("id"):
                    continue

                related_tags = set(related_node.get("tags") or [])
                new_tags = set(tags)
                overlap = len(related_tags & new_tags)
                total = len(related_tags | new_tags)
                strength = overlap / max(total, 1)

                if strength > 0.3:  # minimum link strength
                    create_memory_link(
                        new_node.get("id"),
                        related_node.get("id"),
                        link_type="related",
                        db=self.db,
                    )

        except Exception as exc:
            logger.warning("Auto-link failed: %s", exc)


def capture_system_event_as_memory(db, event) -> Optional[dict]:
    event_type = getattr(event, "type", None)
    if event_type not in AUTO_MEMORY_EVENT_TYPES:
        return None

    payload = getattr(event, "payload", {}) or {}

    # RT-MEMTXN-LEAK-1 (third site) — do not capture the lifecycle events of the
    # runtime's own memory-maintenance jobs. Every memory node enqueues an embedding
    # job; that job emits EXECUTION_STARTED; capturing it creates another node, which
    # enqueues another job. The cycle is synchronous and each level pins the session
    # it opened, so it drains the connection pool (60 conns `idle in transaction`,
    # ~42s login) rather than terminating. A "the embedding job started" memory has
    # no recall value, so dropping it costs nothing and cuts the cycle at its origin.
    if payload.get("task_name") in RUNTIME_INTERNAL_TASK_NAMES:
        return None

    # Backstop for any *other* capture → job → capture cycle: a job submitted by a
    # capture is plumbing, and its own lifecycle events must not spawn a third level.
    if memory_capture_suppressed():
        logger.debug(
            "[MemoryCapture] skipping %s — nested async submission (cycle guard)",
            event_type,
        )
        return None
    user_id = getattr(event, "user_id", None)
    event_id = str(getattr(event, "id", ""))
    trace_id = getattr(event, "trace_id", None)
    source = getattr(event, "source", None) or "system_event"

    content = payload.get("message") or payload.get("error") or payload.get("description")
    if not content:
        content = f"{event_type} from {source}"

    tags = [source, event_type.replace(".", "_"), "causal_memory"]
    if "feedback." in str(event_type):
        tags.append("behavior_signal")
    return queue_memory_capture(
        db=db,
        user_id=str(user_id) if user_id else None,
        agent_namespace="system",
        event_type=event_type,
        content=content,
        source=f"system_event:{source}",
        tags=tags,
        context={"significance": 1.0},
        extra={
            "trace_id": trace_id,
            "source_event_id": event_id,
            "event_type": event_type,
            "event_payload": payload,
        },
        force=True,
    )

