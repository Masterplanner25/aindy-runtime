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
