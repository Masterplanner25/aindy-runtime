"""Production worker entrypoint.

Starts the existing worker lifecycle hooks, then blocks in the distributed
queue consumer. Job execution remains owned by ``worker_loop.process_one_job``.
"""

from __future__ import annotations

import logging
import os
import sys

from AINDY.core.distributed_queue import validate_queue_backend
from AINDY.platform_layer.deployment_contract import (
    PROCESS_ROLE_WORKER,
    publish_worker_runtime_state,
    validate_worker_deployment_profile,
)
from AINDY.platform_layer import scheduler_service
from AINDY.platform_layer import registry
from AINDY.platform_layer.registry import load_plugins
from AINDY.worker import _wait_for_background_schema, lifecycle_services
from AINDY.worker.worker_loop import run_worker_loop

logger = logging.getLogger(__name__)


def _rehydrate_and_open_scheduler(log) -> None:
    """Rehydrate waiting flow runs into this process's scheduler, then open it to bus events."""
    from AINDY.kernel.event_bus import get_event_bus
    from AINDY.kernel.scheduler_engine import get_scheduler_engine

    try:
        from AINDY.core.flow_run_rehydration import rehydrate_waiting_flow_runs
        from AINDY.db.database import SessionLocal

        db = SessionLocal()
        try:
            registered = rehydrate_waiting_flow_runs(db)
        finally:
            db.close()
        if registered:
            log.info("[worker] FlowRun rehydration registered %d run(s)", registered)
    except Exception as exc:  # noqa: BLE001 — an empty registry still beats a closed one
        log.warning("[worker] FlowRun rehydration failed (scheduler opens anyway): %s", exc)
    get_scheduler_engine().mark_rehydration_complete()
    drained = get_event_bus().drain_buffered_events()
    log.info("[worker] scheduler open to bus events; drained %s buffered", drained)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    load_plugins()
    # ★ `load_plugins()` COLLECTS flow registrations; `register_flows()` INVOKES them, and only
    # the second fills FLOW_REGISTRY. The API does both in `startup.py`; the worker did only the
    # first, so its FLOW_REGISTRY was empty — fine while a worker ran jobs, and not fine now
    # that it also rebuilds flow resumes (FR-15). Without this a resumed flow is unrunnable
    # here, and `resume_reconstruction` dead-letters it rather than acknowledging it.
    registry.register_flows()
    # FR-31 — and the RUNTIME-owned flows, which `register_flows()` (plugins only) never
    # registers: a worker rebuilding a `nodus_execute` / `agent_execution` resume must find
    # them by name. `register_all_flows()` is what the API's `_register_flow_engine` runs.
    from AINDY.runtime.flow_definitions import register_all_flows, register_default_flows

    register_all_flows()
    register_default_flows()  # FR-32 — defaults last; same order as the API now
    deployment_profile = validate_worker_deployment_profile()
    publish_worker_runtime_state(
        process_role=PROCESS_ROLE_WORKER,
        startup_complete=False,
        queue_ready=False,
        schema_ready=False,
        scheduler_role="disabled",
        background_leadership_mode=deployment_profile["background_leadership_mode"],
        deployment_profile=deployment_profile["name"],
        deployment_profile_source=deployment_profile["source"],
    )
    lifecycle_started = False

    try:
        validate_queue_backend()
        publish_worker_runtime_state(queue_ready=True)
        schema_ready = _wait_for_background_schema()
        publish_worker_runtime_state(schema_ready=schema_ready)
        if schema_ready:
            lifecycle_started = bool(
                lifecycle_services.start_background_tasks(
                    enable=True,
                    log=logger,
                )
            )
            from AINDY.db.database import SessionLocal
            from AINDY.platform_layer.leadership import (
                background_owner_id,
                get_background_elector,
            )

            # distributed-worker is lease-elected: exactly one worker runs the
            # scheduler, decided by an atomic DB lease with failover (LEASE-1).
            elector = get_background_elector(
                db_factory=SessionLocal,
                owner_id=background_owner_id(),
                on_acquire=scheduler_service.start,
                on_lose=scheduler_service.stop,
                enabled=lifecycle_started,
            )
            if elector.elect_once():
                publish_worker_runtime_state(scheduler_role="leader")
                logger.info(
                    "Worker started scheduler lifecycle as lease leader (owner_id=%s)",
                    elector.owner_id,
                )
            else:
                publish_worker_runtime_state(scheduler_role="follower")
                logger.info(
                    "Worker started without scheduler leadership (owner_id=%s)",
                    elector.owner_id,
                )
            elector.start()
        else:
            raise RuntimeError(
                "Worker startup blocked: required runtime-owned schema is not ready. "
                "Initialize or reconcile the packaged runtime schema before starting the worker."
            )

        # FR-15 silent loss #6 (evidence topology, 2026-09-22): the worker's scheduler engine
        # never had its rehydration marked complete — that happens in the api's lifespan — so
        # every bus event the worker received was buffered and, at 1000, DROPPED
        # ("[Scheduler] pre-rehydration buffer full"). A worker that became background LEADER
        # then held rehydrated wait registrations nothing could wake. Same sequence the api's
        # startup runs: rehydrate what is waiting, mark complete, drain what was buffered.
        _rehydrate_and_open_scheduler(logger)
        concurrency = int(os.getenv("WORKER_CONCURRENCY", "1"))
        publish_worker_runtime_state(startup_complete=True)
        run_worker_loop(concurrency=concurrency)
    finally:
        publish_worker_runtime_state(startup_complete=False)
        try:
            from AINDY.platform_layer.leadership import stop_background_elector

            stop_background_elector()
        except Exception:
            pass
        # Always attempt scheduler stop: a follower may have been promoted to
        # leader (and started the scheduler) after startup via failover.
        scheduler_service.stop()
        if lifecycle_started:
            lifecycle_services.stop_background_tasks(log=logger)


if __name__ == "__main__":
    main()
