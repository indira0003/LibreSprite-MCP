import threading, time
import pytest
from libresprite_mcp.protocol import RelayConfig
from libresprite_mcp.libresprite_proxy import LibrespriteProxy

@pytest.fixture
def proxy():
    return LibrespriteProxy(RelayConfig(port=64829, timeout=0.2))

def test_pairing_is_one_time(proxy):
    c=proxy.app.test_client()
    r=c.post("/pair", json={"bridge_version":"x","storage":True,"storage_fetch":True})
    assert r.status_code == 200
    token=r.get_json()["session_token"]
    assert token
    r2=c.post("/pair", json={"bridge_version":"y"})
    assert r2.status_code == 409

def test_next_requires_token(proxy):
    c=proxy.app.test_client()
    assert c.get("/next").status_code == 401

def test_unknown_result_rejected(proxy):
    c=proxy.app.test_client()
    token=c.post("/pair", json={"bridge_version":"x"}).get_json()["session_token"]
    r=c.post("/result", json={"request_id":"stale","ok":True}, headers={"X-LibreSprite-Token":token})
    assert r.status_code == 409

def test_execute_timeout_cleans_up(proxy):
    with pytest.raises(TimeoutError):
        proxy.execute("health")
    assert not proxy._inflight

def test_request_response_id_matching(proxy):
    c=proxy.app.test_client()
    token=c.post("/pair", json={"bridge_version":"x"}).get_json()["session_token"]
    holder={}
    def worker():
        holder["result"]=proxy.execute("health")
    t=threading.Thread(target=worker); t.start()
    time.sleep(0.02)
    nxt=c.get("/next", query_string={"token":token}).get_json()
    rid=nxt["request_id"]
    c.post("/result", json={"request_id":rid,"ok":True,"result":{"hello":"world"}}, headers={"X-LibreSprite-Token":token})
    t.join(1)
    assert holder["result"]["request_id"] == rid
    assert holder["result"]["result"]["hello"] == "world"
