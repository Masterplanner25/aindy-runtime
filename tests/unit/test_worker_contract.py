from __future__ import annotations

import http.client
import json

import pytest

from AINDY.worker.health_server import WorkerHealthServer

pytestmark = pytest.mark.runtime_only


def test_worker_health_server_constructs_with_defaults():
    server = WorkerHealthServer()
    assert server._host == "0.0.0.0"
    assert server._port == 8001
    assert server._server is None
    assert server._thread is None


def test_worker_health_server_register_check_stores_callable():
    server = WorkerHealthServer(port=0)
    server.register_check("alive", lambda: True)
    server.register_check("ready", lambda: False)
    assert "alive" in server._checks
    assert "ready" in server._checks


def test_worker_health_server_start_binds_and_stop_cleans_up():
    server = WorkerHealthServer(host="127.0.0.1", port=0)
    server.start()
    try:
        assert server._server is not None
        assert server._thread is not None
        assert server._thread.is_alive()
        assert server._server.server_address[1] > 0
    finally:
        server.stop()
    assert server._server is None
    assert server._thread is None


def test_worker_health_server_returns_200_when_all_checks_pass():
    server = WorkerHealthServer(host="127.0.0.1", port=0)
    server.register_check("ok", lambda: True)
    server.start()
    try:
        port = server._server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        assert resp.status == 200
        assert body["status"] == "healthy"
        assert body["checks"]["ok"]["ok"] is True
    finally:
        server.stop()


def test_worker_health_server_returns_503_when_a_check_fails():
    server = WorkerHealthServer(host="127.0.0.1", port=0)
    server.register_check("broken", lambda: False)
    server.start()
    try:
        port = server._server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        assert resp.status == 503
        assert body["status"] == "unhealthy"
        assert body["checks"]["broken"]["ok"] is False
    finally:
        server.stop()


def test_worker_health_server_returns_404_for_unknown_path():
    server = WorkerHealthServer(host="127.0.0.1", port=0)
    server.start()
    try:
        port = server._server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/not-a-real-path")
        resp = conn.getresponse()
        assert resp.status == 404
    finally:
        server.stop()


def test_worker_health_server_start_is_idempotent():
    server = WorkerHealthServer(host="127.0.0.1", port=0)
    server.start()
    first_server = server._server
    server.start()
    assert server._server is first_server
    server.stop()
