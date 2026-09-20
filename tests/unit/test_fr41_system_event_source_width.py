"""FR-41 — `system_events.source` fits a dotted route name, and one that cannot fit is refused
at pipeline ENTRY as a contract violation, not failed per request at WARNING.

The filed shape: `source = Column(String(32))`; the pipeline writes every request's route name
there; a 33+ character name raised `StringDataRightTruncation` inside the REQUIRED
`execution.started` emit, `_safe_emit_event` caught it, logged a WARNING, recorded the side
effect `failed`, and the request proceeded with no execution record at all. The app that found
it had 8 of 230 route names over the width, unrecorded since 2026-09-10.

Three claims, each with a control that makes it fail:
  * the width is 128 and is READ OFF THE MODEL (a 32 restored in the model turns the filed
    38-character name red here, not in an app log);
  * a route named at exactly the width answers 200 through the real pipeline;
  * one character over: under `ENFORCE_EXECUTION_CONTRACT` (the default) the request FAILS
    before the handler runs; with enforcement off it proceeds and the violation is logged at
    ERROR once per name, not per request.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from AINDY.core.execution_helper import execute_with_pipeline

pytestmark = pytest.mark.runtime_only

_FILED_LONGEST = "masterplan.strategy.conclusion.propose"  # 38 — the app's longest, unrecorded at 32


@pytest.fixture(autouse=True)
def _fresh_once_registry():
    from AINDY.core import system_event_service as svc

    saved = set(svc._oversize_sources_reported)
    svc._oversize_sources_reported.clear()
    yield
    svc._oversize_sources_reported.clear()
    svc._oversize_sources_reported.update(saved)


def test_the_width_is_read_off_the_model_and_is_128():
    from AINDY.core.system_event_service import system_event_source_max_length
    from AINDY.db.models.system_event import SystemEvent

    assert system_event_source_max_length() == SystemEvent.__table__.c.source.type.length == 128


def test_the_filed_route_name_fits():
    from AINDY.core.system_event_service import check_system_event_source

    assert len(_FILED_LONGEST) == 38
    assert check_system_event_source(_FILED_LONGEST) is None


def test_one_over_the_width_is_a_violation_logged_once_per_name(caplog):
    from AINDY.core.system_event_service import check_system_event_source, system_event_source_max_length

    limit = system_event_source_max_length()
    fits = "r." + "x" * (limit - 2)
    over = fits + "y"
    assert check_system_event_source(fits) is None

    with caplog.at_level(logging.ERROR, logger="AINDY.core.system_event_service"):
        first = check_system_event_source(over)
        second = check_system_event_source(over)
        third = check_system_event_source(over + "z")  # a different name is reported on its own
    assert first and "ExecutionContract violation" in first and str(limit) in first
    assert second == first, "the violation is still a violation on the second request"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "system_events.source" in r.getMessage()]
    assert len(errors) == 2, [r.getMessage()[:60] for r in errors]  # once for `over`, once for `over+z`
    assert "truncat" in first.lower()  # the message says the source is never truncated


def _app_with_route(route_name: str) -> FastAPI:
    router = APIRouter()

    @router.get("/probe")
    async def probe(request: Request):
        async def handler(ctx):
            return {"ran": True}

        return await execute_with_pipeline(request=request, route_name=route_name, handler=handler)

    app = FastAPI()
    app.include_router(router)
    return app


class TestThroughTheRoute:
    """A route test must call the route — the status code is the contract."""

    def test_a_name_at_exactly_the_width_answers_200(self):
        from AINDY.core.system_event_service import system_event_source_max_length

        name = "apps.fr41." + "x" * (system_event_source_max_length() - len("apps.fr41."))
        with TestClient(_app_with_route(name)) as client:
            response = client.get("/probe")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["ran"] is True

    def test_one_over_is_refused_before_the_handler_under_enforcement(self, monkeypatch):
        from AINDY.config import settings
        from AINDY.core.system_event_service import system_event_source_max_length

        monkeypatch.setattr(settings, "ENFORCE_EXECUTION_CONTRACT", True)
        name = "apps.fr41." + "x" * (system_event_source_max_length() - len("apps.fr41.") + 1)
        ran: list[bool] = []
        router = APIRouter()

        @router.get("/probe")
        async def probe(request: Request):
            async def handler(ctx):
                ran.append(True)
                return {"ran": True}

            return await execute_with_pipeline(request=request, route_name=name, handler=handler)

        app = FastAPI()
        app.include_router(router)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/probe")
        assert response.status_code == 500, response.text
        assert ran == [], "the handler ran on a request the contract refused"

    def test_one_over_proceeds_when_enforcement_is_off(self, monkeypatch):
        from AINDY.config import settings
        from AINDY.core.system_event_service import system_event_source_max_length

        monkeypatch.setattr(settings, "ENFORCE_EXECUTION_CONTRACT", False)
        name = "apps.fr41." + "x" * (system_event_source_max_length() - len("apps.fr41.") + 1)
        with TestClient(_app_with_route(name)) as client:
            first = client.get("/probe")
            second = client.get("/probe")
        assert first.status_code == 200 and second.status_code == 200
        from AINDY.core import system_event_service as svc

        assert name in svc._oversize_sources_reported  # reported once, then remembered
