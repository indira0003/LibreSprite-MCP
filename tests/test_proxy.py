import threading
import time

import pytest

from libresprite_mcp.libresprite_proxy import LibrespriteProxy
from libresprite_mcp.protocol import RelayConfig


@pytest.fixture
def proxy():
    return LibrespriteProxy(RelayConfig(port=64829, timeout=0.2))


def pair(client):
    response = client.post(
        "/pair",
        json={"bridge_version": "x", "storage": True, "storage_fetch": True},
    )
    assert response.status_code == 200
    return response.get_json()["session_token"]


def test_ping_does_not_mark_bridge_connected(proxy):
    client = proxy.app.test_client()
    assert client.get("/ping").status_code == 200
    assert proxy.connected is False


def test_pairing_requires_storage_fetch(proxy):
    client = proxy.app.test_client()
    response = client.post("/pair", json={"bridge_version": "x", "storage_fetch": False})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "STORAGE_FETCH_UNAVAILABLE"


def test_pairing_is_one_time(proxy):
    client = proxy.app.test_client()
    token = pair(client)
    assert token
    second = client.post(
        "/pair",
        json={"bridge_version": "y", "storage": True, "storage_fetch": True},
    )
    assert second.status_code == 409


def test_next_requires_header_token(proxy):
    client = proxy.app.test_client()
    token = pair(client)
    assert client.get("/next", query_string={"token": token}).status_code == 401


def test_bad_result_token_rejected(proxy):
    client = proxy.app.test_client()
    pair(client)
    response = client.post(
        "/result",
        json={"request_id": "anything", "ok": True},
        headers={"X-LibreSprite-Token": "wrong"},
    )
    assert response.status_code == 401


def test_unknown_result_rejected(proxy):
    client = proxy.app.test_client()
    token = pair(client)
    response = client.post(
        "/result",
        json={"request_id": "stale", "ok": True},
        headers={"X-LibreSprite-Token": token},
    )
    assert response.status_code == 409


def test_execute_timeout_cleans_up(proxy):
    with pytest.raises(TimeoutError):
        proxy.execute("health")
    assert not proxy._inflight


def test_request_response_id_matching(proxy):
    client = proxy.app.test_client()
    token = pair(client)
    holder = {}

    def worker():
        holder["result"] = proxy.execute("health")

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.02)
    next_response = client.get(
        "/next",
        headers={"X-LibreSprite-Token": token},
    ).get_json()
    request_id = next_response["request_id"]
    client.post(
        "/result",
        json={"request_id": request_id, "ok": True, "result": {"hello": "world"}},
        headers={"X-LibreSprite-Token": token},
    )
    thread.join(1)
    assert holder["result"]["request_id"] == request_id
    assert holder["result"]["result"]["hello"] == "world"
