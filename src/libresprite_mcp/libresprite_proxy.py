from __future__ import annotations

import hmac
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from .protocol import (
    MAX_JSON_BYTES,
    PROTOCOL_VERSION,
    RelayConfig,
    new_request_id,
    new_session_token,
    validate_operation,
    validate_payload_size,
)


@dataclass
class Pending:
    request_id: str
    operation: str
    payload: dict[str, Any]
    event: threading.Event
    result: dict[str, Any] | None = None


class LibrespriteProxy:
    def __init__(self, config: RelayConfig):
        self.config = config
        self.session_token = new_session_token()
        self.app = Flask(__name__)
        self._pending_q: queue.Queue[Pending] = queue.Queue(maxsize=64)
        self._inflight: dict[str, Pending] = {}
        self._lock = threading.Lock()
        self._server = None
        self._server_thread: threading.Thread | None = None
        self._last_seen = 0.0
        self._bridge_info: dict[str, Any] = {}
        self._setup_routes()

    @property
    def connected(self) -> bool:
        return bool(self._bridge_info) and (time.monotonic() - self._last_seen) < 10.0

    @property
    def bridge_info(self) -> dict[str, Any]:
        return dict(self._bridge_info)

    def _auth_ok(self, token: str | None) -> bool:
        if not token:
            return False
        return hmac.compare_digest(token.encode("utf-8"), self.session_token.encode("utf-8"))

    @staticmethod
    def _error(code: str, message: str, status: int):
        return jsonify({"ok": False, "error": {"code": code, "message": message}}), status

    def _setup_routes(self):
        @self.app.before_request
        def reject_large_body():
            if request.content_length and request.content_length > MAX_JSON_BYTES:
                return self._error("PAYLOAD_TOO_LARGE", "request too large", 413)
            return None

        @self.app.get("/ping")
        def ping():
            # Intentionally unauthenticated for discovery, and intentionally does not
            # mark the bridge connected. Only pairing/authenticated traffic does that.
            return jsonify(
                {
                    "ok": True,
                    "status": "pong",
                    "protocol_version": PROTOCOL_VERSION,
                    "mode": self.config.mode,
                }
            )

        @self.app.post("/pair")
        def pair():
            """One-time loopback pairing. First bridge receives the random session token."""
            raw = request.get_data(cache=True)
            try:
                validate_payload_size(raw)
            except ValueError:
                return self._error("PAYLOAD_TOO_LARGE", "request too large", 413)

            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return self._error("INVALID_JSON", "JSON object required", 400)
            if self._bridge_info:
                return self._error("ALREADY_PAIRED", "relay already paired", 409)
            if not data.get("storage_fetch"):
                return self._error(
                    "STORAGE_FETCH_UNAVAILABLE",
                    "LibreSprite bridge reported that storage.fetch is unavailable",
                    400,
                )

            self._last_seen = time.monotonic()
            self._bridge_info = {
                "bridge_version": data.get("bridge_version"),
                "libresprite_version": data.get("libresprite_version"),
                "platform": data.get("platform"),
                "storage": bool(data.get("storage")),
                "storage_fetch": bool(data.get("storage_fetch")),
            }
            return jsonify(
                {
                    "ok": True,
                    "protocol_version": PROTOCOL_VERSION,
                    "mode": self.config.mode,
                    "session_token": self.session_token,
                }
            )

        @self.app.get("/next")
        def next_operation():
            token = request.headers.get("X-LibreSprite-Token")
            if not self._auth_ok(token):
                return self._error("UNAUTHORIZED", "bad session token", 401)
            self._last_seen = time.monotonic()
            try:
                pending = self._pending_q.get(timeout=2.0)
            except queue.Empty:
                return jsonify({"ok": True, "idle": True})

            with self._lock:
                self._inflight[pending.request_id] = pending
            return jsonify(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": pending.request_id,
                    "operation": pending.operation,
                    "payload": pending.payload,
                }
            )

        @self.app.post("/result")
        def post_result():
            token = request.headers.get("X-LibreSprite-Token")
            if not self._auth_ok(token):
                return self._error("UNAUTHORIZED", "bad session token", 401)

            raw = request.get_data(cache=True)
            try:
                validate_payload_size(raw)
            except ValueError:
                return self._error("PAYLOAD_TOO_LARGE", "request too large", 413)

            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return self._error("INVALID_JSON", "JSON object required", 400)
            rid = data.get("request_id")
            if not isinstance(rid, str):
                return self._error("MISSING_REQUEST_ID", "request_id required", 400)

            with self._lock:
                pending = self._inflight.pop(rid, None)
            if pending is None:
                return self._error("UNKNOWN_REQUEST_ID", "stale or unknown request", 409)

            pending.result = data
            pending.event.set()
            self._last_seen = time.monotonic()
            return jsonify({"ok": True})

    def start(self):
        if self._server_thread and self._server_thread.is_alive():
            return
        self._server = make_server(self.config.host, self.config.port, self.app, threaded=True)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()

    def execute(self, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        validate_operation(operation, self.config.mode)
        pending = Pending(new_request_id(), operation, payload or {}, threading.Event())
        try:
            self._pending_q.put(pending, timeout=1.0)
        except queue.Full as exc:
            raise RuntimeError("relay queue is full") from exc

        try:
            if not pending.event.wait(timeout=self.config.timeout):
                with self._lock:
                    self._inflight.pop(pending.request_id, None)
                raise TimeoutError(f"LibreSprite operation timed out: {operation}")
            if pending.result is None:
                raise RuntimeError("LibreSprite returned no result")
            if pending.result.get("request_id") != pending.request_id:
                raise RuntimeError("request_id mismatch")
            return pending.result
        finally:
            with self._lock:
                self._inflight.pop(pending.request_id, None)
