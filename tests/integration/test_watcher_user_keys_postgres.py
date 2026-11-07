"""A self-minted watcher key on real Postgres, where `platform_api_keys.scopes` is a real ARRAY.

The unit suite swaps that column for JSON, because SQLite can't bind an ARRAY. This covers what
the swap hides: the scope list read back from the row deciding what the key may do.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.integration


def test_a_minted_key_is_confined_and_acts_only_for_its_owner(client, test_user, auth_headers, monkeypatch):
    import AINDY.routes.watcher_router  # noqa: F401  (the package attribute is the router)

    seen: list[dict] = []

    def _flow(flow_name, payload, db, user_id):
        seen.append({"user_id": user_id, "signals": payload["signals"]})
        return {"accepted": len(payload["signals"]), "session_ended_count": 0}

    monkeypatch.setattr(sys.modules["AINDY.routes.watcher_router"], "_run_flow_watcher", _flow)

    created = client.post("/watcher/keys", json={"name": "laptop"}, headers=auth_headers)
    assert created.status_code == 201, created.text
    assert created.json()["scopes"] == ["watcher.signals"]
    key = {"X-Platform-Key": created.json()["key"]}

    posted = client.post(
        "/watcher/signals",
        json={
            "signals": [
                {
                    "signal_type": "heartbeat",
                    "session_id": "s",
                    "timestamp": "2026-11-07T12:00:00Z",
                    "app_name": "editor",
                    "activity_type": "work",
                }
            ]
        },
        headers=key,
    )
    assert posted.status_code == 201, posted.text
    assert seen[0]["user_id"] == str(test_user.id)
    assert seen[0]["signals"][0]["user_id"] == str(test_user.id)

    assert client.get("/platform/flows", headers=key).status_code == 403
