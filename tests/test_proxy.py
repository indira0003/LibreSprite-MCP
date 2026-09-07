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
    client = proxy.app.test_client()
    token = pair(client)
    with pytest.raises(TimeoutError):
        proxy.execute("health")
    assert not proxy._inflight
    assert not proxy._pending
    assert client.get("/next", headers={"X-LibreSprite-Token": token}).get_json()["idle"]


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


def test_disconnected_fails_without_queuing(proxy):
    with pytest.raises(ConnectionError, match="not connected"):
        proxy.execute("draw_pixels")
    assert not proxy._pending


def test_idle_poll_is_immediate(proxy):
    client = proxy.app.test_client()
    token = pair(client)
    start = time.monotonic()
    for _ in range(5):
        response = client.get("/next", headers={"X-LibreSprite-Token": token})
        assert response.get_json()["idle"] is True
    assert time.monotonic() - start < 0.5


def test_stale_pair_releases_and_rotates_token(proxy):
    client = proxy.app.test_client()
    old = pair(client)
    proxy._last_seen -= proxy.config.lease_seconds + 1
    assert not proxy.connected
    assert proxy.bridge_info == {}
    new = pair(client)
    assert new != old
    for path in ("/next", "/result", "/disconnect"):
        call = client.get if path == "/next" else client.post
        assert call(path, headers={"X-LibreSprite-Token": old}).status_code == 401
    assert client.get("/next", headers={"X-LibreSprite-Token": new}).status_code == 200


def test_stale_pair_can_be_replaced_without_status_read(proxy):
    client = proxy.app.test_client()
    old = pair(client)
    proxy._last_seen -= proxy.config.lease_seconds + 1
    assert pair(client) != old


def test_lost_pair_response_is_idempotent(proxy):
    client = proxy.app.test_client()
    body = {"bridge_id": "random-instance-0123456789", "storage_fetch": True}
    a = client.post("/pair", json=body).get_json()["session_token"]
    b = client.post("/pair", json=body).get_json()["session_token"]
    assert a == b
    body["bridge_id"] = "different-instance-012345"
    assert client.post("/pair", json=body).status_code == 409


def test_disconnect_allows_immediate_reconnect(proxy):
    client = proxy.app.test_client()
    old = pair(client)
    assert client.post("/disconnect", headers={"X-LibreSprite-Token": old}).status_code == 200
    assert not proxy.connected
    assert pair(client) != old


def queued_worker(proxy, operation="health"):
    holder = {}
    def work():
        try:
            holder["result"] = proxy.execute(operation)
        except Exception as exc:
            holder["error"] = exc
    thread = threading.Thread(target=work)
    thread.start()
    limit = time.monotonic() + 1
    while not proxy._pending and time.monotonic() < limit:
        time.sleep(0.001)
    return thread, holder


def test_redelivery_and_duplicate_result_do_not_repeat_operations(proxy):
    client = proxy.app.test_client()
    headers = {"X-LibreSprite-Token": pair(client)}
    thread, holder = queued_worker(proxy)
    a = client.get("/next", headers=headers).get_json()
    b = client.get("/next", headers=headers).get_json()
    assert a == b
    result = {"request_id": a["request_id"], "ok": True, "result": {"written": 1}}
    assert client.post("/result", json=result, headers=headers).status_code == 200
    assert client.post("/result", json=result, headers=headers).get_json()["duplicate"]
    thread.join(1)
    assert holder["result"] == result
    assert client.get("/next", headers=headers).get_json()["idle"]
    result["result"] = {"written": 2}
    assert client.post("/result", json=result, headers=headers).status_code == 409


@pytest.mark.parametrize("delivered", [False, True])
def test_pair_replacement_cancels_old_requests(proxy, delivered):
    client = proxy.app.test_client()
    headers = {"X-LibreSprite-Token": pair(client)}
    thread, holder = queued_worker(proxy)
    if delivered:
        client.get("/next", headers=headers)
    proxy._last_seen -= proxy.config.lease_seconds + 1
    new = pair(client)
    thread.join(1)
    assert isinstance(holder["error"], ConnectionError)
    assert client.get("/next", headers={"X-LibreSprite-Token": new}).get_json()["idle"]


def test_delivered_timeout_reports_unknown_outcome_and_accepts_next_poll(proxy):
    client = proxy.app.test_client()
    headers = {"X-LibreSprite-Token": pair(client)}
    thread, holder = queued_worker(proxy)
    req = client.get("/next", headers=headers).get_json()
    thread.join(1)
    assert isinstance(holder["error"], TimeoutError)
    assert "outcome unknown" in str(holder["error"])
    assert client.post("/result", json={"request_id": req["request_id"], "ok": True}, headers=headers).status_code == 409
    assert client.get("/next", headers=headers).get_json()["idle"]
    assert proxy.connected


def test_pair_race_has_one_winner(proxy):
    barrier = threading.Barrier(2)
    statuses = []
    def work():
        with proxy.app.test_client() as client:
            barrier.wait()
            statuses.append(client.post("/pair", json={"storage_fetch": True}).status_code)
    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(1)
    assert sorted(statuses) == [200, 409]


def test_bad_result_does_not_consume_pending(proxy):
    client = proxy.app.test_client()
    headers = {"X-LibreSprite-Token": pair(client)}
    thread, holder = queued_worker(proxy)
    req = client.get("/next", headers=headers).get_json()
    result = {"request_id": req["request_id"], "ok": "yes"}
    assert client.post("/result", json=result, headers=headers).status_code == 400
    result["ok"] = True
    assert client.post("/result", json=result, headers=headers).status_code == 200
    thread.join(1)
    assert holder["result"]["ok"]


def test_origin_host_and_size_rejected(proxy):
    client = proxy.app.test_client()
    assert client.post("/pair", json={"storage_fetch": True}, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.get("/ping", headers={"Host": "evil.invalid"}).status_code == 403
    proxy.app.config["MAX_CONTENT_LENGTH"] = 32
    assert client.post("/pair", data="x" * 64, content_type="application/json").status_code == 413


def test_concurrent_next_requests_receive_same_inflight_id(proxy):
    token = pair(proxy.app.test_client())
    thread, holder = queued_worker(proxy)
    barrier = threading.Barrier(2)
    replies = []
    def poll():
        with proxy.app.test_client() as client:
            barrier.wait()
            replies.append(client.get("/next", headers={"X-LibreSprite-Token": token}).get_json())
    pollers = [threading.Thread(target=poll) for _ in range(2)]
    for poller in pollers:
        poller.start()
    for poller in pollers:
        poller.join(1)
    assert replies[0]["request_id"] == replies[1]["request_id"]
    proxy.app.test_client().post("/result", headers={"X-LibreSprite-Token": token},
        json={"request_id": replies[0]["request_id"], "ok": True})
    thread.join(1)
    assert holder["result"]["ok"]
