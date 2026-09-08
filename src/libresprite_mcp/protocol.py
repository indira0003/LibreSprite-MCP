from __future__ import annotations

import os
import math
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

PROTOCOL_VERSION = 1
DEFAULT_PORT = 64823
DEFAULT_TIMEOUT = 20.0
MAX_JSON_BYTES = 16_000_000
MAX_PIXELS = 262_144
MAX_IMAGE_BYTES = 8_000_000

SAFE_OPERATIONS = {
    "health",
    "get_sprite_info",
    "get_active_frame",
    "set_active_frame",
    "get_frames",
    "get_layers",
    "add_frame",
    "insert_frame",
    "duplicate_frame",
    "delete_frame",
    "get_frame_durations",
    "set_frame_duration",
    "set_frame_durations",
    "create_layer",
    "delete_layer",
    "rename_layer",
    "set_layer_visibility",
    "set_active_layer",
    "get_cel",
    "copy_cel",
    "move_cel",
    "clear_cel",
    "get_pixel",
    "get_pixels",
    "draw_pixels",
    "get_region",
    "set_region",
    "copy_region",
    "move_region",
    "clear_region",
    "flip_region",
    "get_image_data",
    "set_image_data",
    "get_palette",
    "set_palette_color",
    "get_tags",
    "create_tag",
    "delete_tag",
    "render_frame_preview",
    "export_png",
    "export_gif",
    "export_spritesheet",
    "save_sprite",
    "save_as",
    "save_copy",
    "open_sprite",
}


@dataclass(frozen=True)
class RelayConfig:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT
    mode: str = "safe"
    allowed_root: str | None = None
    lease_seconds: float = 30.0

    def __post_init__(self):
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Relay host must be loopback-only")
        if self.mode not in {"safe", "dev"}:
            raise ValueError("mode must be 'safe' or 'dev'")
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be 0..65535")
        if any(not math.isfinite(v) or v <= 0 for v in (self.timeout, self.lease_seconds)):
            raise ValueError("timeout and lease_seconds must be finite and positive")


def new_request_id() -> str:
    return str(uuid.uuid4())


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def validate_payload_size(raw: bytes) -> None:
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("payload too large")


def validate_operation(operation: str, mode: str) -> None:
    if mode == "dev" and operation == "run_script":
        return
    if operation not in SAFE_OPERATIONS:
        raise ValueError(f"operation not allowed: {operation}")


def normalize_allowed_path(path: str, allowed_root: str | None, *, must_exist: bool = False) -> str:
    if not path:
        raise ValueError("path is required")
    p = Path(path).expanduser()
    if os.name == "nt":
        text = str(p)
        if text.startswith("\\\\"):
            raise ValueError("UNC paths are not allowed in SAFE mode")
        name = p.name.rstrip(". ").upper()
        reserved = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if name.split(".")[0] in reserved:
            raise ValueError("Windows device path is not allowed")
    p = p.resolve(strict=False)
    if allowed_root:
        root = Path(allowed_root).expanduser().resolve(strict=False)
        try:
            p.relative_to(root)
        except ValueError as exc:
            raise ValueError("path is outside the allowed root") from exc
    if must_exist and not p.exists():
        raise ValueError("path does not exist")
    return str(p)
