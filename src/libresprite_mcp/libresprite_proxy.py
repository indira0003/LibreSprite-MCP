from __future__ import annotations

import hmac
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from .protocol import MAX_JSON_BYTES, PROTOCOL_VERSION, RelayConfig, new_request_id, new_session_token, validate_operation


@dataclass
class Pending:
    request_id: str
    operation: str
    payload: dict[str, Any]
    deadline: float
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: Exception | None = None
    delivered: bool = False


class LibrespriteProxy:
    """One leased bridge, serialized delivery, bounded deduplication.

    Session/queue transitions share one lock; HTTP handlers never long-poll.
    Delivered operations can have unknown outcomes on timeout. Requests are
    never silently resubmitted under new IDs or carried into a new session.
    """

    def __init__(self, config: RelayConfig):
        self.config = config
        self.session_token = new_session_token()
        self.app = Flask(__name__)
        self.app.config["MAX_CONTENT_LENGTH"] = MAX_JSON_BYTES
        self._pending: OrderedDict[str, Pending] = OrderedDict()
        self._inflight: dict[str, Pending] = {}
        self._completed: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._server = None
        self._server_thread: threading.Thread | None = None
        self._last_seen = 0.0
        self._bridge_info: dict[str, Any] = {}
        self._bridge_id: str | None = None
        self._setup_routes()

    def _reset_session(self, reason: str) -> None:
        for pending in self._pending.values():
            suffix = "; delivered operation outcome is unknown" if pending.delivered else "; operation was not delivered"
            pending.error = ConnectionError(reason + suffix)
            pending.event.set()
        self._pending.clear()
        self._inflight.clear()
        self._completed.clear()
        self._bridge_info = {}
        self._bridge_id = None
        self._last_seen = 0.0
        self.session_token = new_session_token()

    def _expire(self) -> None:
        now = time.monotonic()
        if self._bridge_info and now - self._last_seen >= self.config.lease_seconds:
            self._reset_session("bridge lease expired")
        for pending in list(self._pending.values()):
            if now >= pending.deadline:
                self._timeout(pending)

    def _timeout(self, pending: Pending) -> None:
        self._pending.pop(pending.request_id, None)
        self._inflight.pop(pending.request_id, None)
        suffix = "; outcome unknown, inspect sprite before retrying" if pending.delivered else "; not delivered"
        pending.error = TimeoutError(f"LibreSprite operation timed out: {pending.operation}{suffix}")
        pending.event.set()

    @property
    def connected(self) -> bool:
        with self._lock:
            self._expire()
            return bool(self._bridge_info)

    @property
    def bridge_info(self) -> dict[str, Any]:
        with self._lock:
            self._expire()
            return dict(self._bridge_info)

    def _auth_ok(self) -> bool:
        self._expire()
        token = request.headers.get("X-LibreSprite-Token")
        return bool(self._bridge_info and token) and hmac.compare_digest(
            token.encode("utf-8"), self.session_token.encode("utf-8")
        )

    @staticmethod
    def _error(code: str, message: str, status: int):
        return jsonify({"ok": False, "error": {"code": code, "message": message}}), status

    def _setup_routes(self):
        @self.app.errorhandler(413)
        def too_large(_):
            return self._error("PAYLOAD_TOO_LARGE", "request too large", 413)

        @self.app.before_request
        def validate_request():
            # Desktop-only transport: reject browser-origin and DNS-rebinding
            # requests. No CORS access is granted.
            if request.headers.get("Origin"):
                return self._error("ORIGIN_FORBIDDEN", "desktop bridge required", 403)
            try:
                hostname = urlsplit("http://" + request.host).hostname
            except ValueError:
                hostname = None
            if hostname not in {"127.0.0.1", "localhost", "::1"}:
                return self._error("HOST_FORBIDDEN", "loopback Host required", 403)
            if request.content_length and request.content_length > MAX_JSON_BYTES:
                return too_large(None)
            return None

        @self.app.after_request
        def no_cache(response):
            response.headers["Cache-Control"] = "no-store"
            return response

        @self.app.get("/ping")
        def ping():
            return jsonify(ok=True, status="pong", protocol_version=PROTOCOL_VERSION, mode=self.config.mode)

        @self.app.post("/pair")
        def pair():
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return self._error("INVALID_JSON", "JSON object required", 400)
            if data.get("storage_fetch") is not True:
                return self._error("STORAGE_FETCH_UNAVAILABLE", "storage.fetch unavailable", 400)
            bridge_id = data.get("bridge_id")
            if bridge_id is not None and (not isinstance(bridge_id, str) or not 16 <= len(bridge_id) <= 128):
                return self._error("INVALID_BRIDGE_ID", "bridge_id must be 16..128 characters", 400)
            if data.get("protocol_version", PROTOCOL_VERSION) != PROTOCOL_VERSION:
                return self._error("PROTOCOL_MISMATCH", "unsupported bridge protocol", 400)
            with self._lock:
                self._expire()
                if self._bridge_info:
                    # A per-script nonce makes a lost /pair reply retryable.
                    # Another live bridge still cannot replace this lease.
                    if not bridge_id or bridge_id != self._bridge_id:
                        return self._error("ALREADY_PAIRED", "another bridge has an active lease; retry later", 409)
                else:
                    self.session_token = new_session_token()
                    self._bridge_id = bridge_id
                    self._bridge_info = {
                        key: data.get(key) for key in
                        ("bridge_version", "libresprite_version", "platform", "storage", "storage_fetch")
                    }
                self._last_seen = time.monotonic()
                return jsonify(ok=True, protocol_version=PROTOCOL_VERSION, mode=self.config.mode,
                               session_token=self.session_token, lease_seconds=self.config.lease_seconds,
                               poll_interval_ms=500)

        @self.app.post("/disconnect")
        def disconnect():
            with self._lock:
                if not self._auth_ok():
                    return self._error("UNAUTHORIZED", "bad or expired session token", 401)
                self._reset_session("bridge disconnected")
                return jsonify(ok=True)

        @self.app.get("/next")
        def next_operation():
            with self._lock:
                if not self._auth_ok():
                    return self._error("UNAUTHORIZED", "bad or expired session token", 401)
                self._last_seen = time.monotonic()
                # Retry the same ID on a lost HTTP reply; bridge caches results.
                pending = next(iter(self._inflight.values()), None)
                if pending is None:
                    pending = next(iter(self._pending.values()), None)
                if pending is None:
                    return jsonify(ok=True, idle=True, poll_interval_ms=500)
                pending.delivered = True
                self._inflight[pending.request_id] = pending
                return jsonify(protocol_version=PROTOCOL_VERSION, request_id=pending.request_id,
                               operation=pending.operation, payload=pending.payload)

        @self.app.post("/result")
        def post_result():
            with self._lock:
                if not self._auth_ok():
                    return self._error("UNAUTHORIZED", "bad or expired session token", 401)
                data = request.get_json(silent=True)
                if not isinstance(data, dict):
                    return self._error("INVALID_JSON", "JSON object required", 400)
                rid = data.get("request_id")
                if not isinstance(rid, str):
                    return self._error("MISSING_REQUEST_ID", "request_id required", 400)
                if type(data.get("ok")) is not bool:
                    return self._error("INVALID_RESULT", "boolean ok required", 400)
                if data.get("protocol_version", PROTOCOL_VERSION) != PROTOCOL_VERSION:
                    return self._error("PROTOCOL_MISMATCH", "unsupported result protocol", 400)
                self._last_seen = time.monotonic()
                if rid in self._completed:
                    if data != self._completed[rid]:
                        return self._error("RESULT_CONFLICT", "different result for completed request", 409)
                    return jsonify(ok=True, duplicate=True)
                pending = self._inflight.pop(rid, None)
                if pending is None:
                    return self._error("UNKNOWN_REQUEST_ID", "stale or unknown request", 409)
                self._pending.pop(rid, None)
                self._completed[rid] = data
                while len(self._completed) > 2:
                    self._completed.popitem(last=False)
                pending.result = data
                pending.event.set()
                return jsonify(ok=True)

    def start(self):
        if self._server_thread and self._server_thread.is_alive():
            return
        self._server = make_server(self.config.host, self.config.port, self.app, threaded=True)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()

    def stop(self):
        with self._lock:
            self._reset_session("relay stopped")
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._server_thread:
            self._server_thread.join(timeout=2)
            self._server_thread = None

    def execute(self, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        validate_operation(operation, self.config.mode)
        payload = payload or {}
        if len(json.dumps(payload, ensure_ascii=True).encode()) > MAX_JSON_BYTES - 1024:
            raise ValueError("payload too large")
        with self._lock:
            self._expire()
            if not self._bridge_info:
                raise ConnectionError("LibreSprite bridge is not connected")
            if len(self._pending) >= 64:
                raise RuntimeError("relay queue is full")
            pending = Pending(new_request_id(), operation, payload, time.monotonic() + self.config.timeout)
            self._pending[pending.request_id] = pending
        pending.event.wait(timeout=self.config.timeout)
        with self._lock:
            if not pending.event.is_set():
                self._timeout(pending)
            if pending.error:
                raise pending.error
            if pending.result is None:
                raise RuntimeError("LibreSprite returned no result")
            return pending.result
