from __future__ import annotations

import base64
import io
import math
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage
from PIL import ImageChops, ImageDraw

from .libresprite_proxy import LibrespriteProxy
from .protocol import MAX_IMAGE_BYTES, MAX_PIXELS, normalize_allowed_path


def _unwrap(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is False:
        return response
    result = response.get("result")
    return result if isinstance(result, dict) else response


def _decode_png_data_uri(uri: str) -> bytes:
    if uri.startswith("data:image/png;base64,"):
        uri = uri.split(",", 1)[1]
    data = base64.b64decode(uri, validate=True)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("PNG result exceeds safety limit")
    return data


def _layer_image(layer: dict[str, Any]) -> PILImage.Image:
    uri = layer.get("png_data_uri")
    if isinstance(uri, str):
        return PILImage.open(io.BytesIO(_decode_png_data_uri(uri))).convert("RGBA")

    rgba = layer.get("rgba")
    width = int(layer.get("width", 0))
    height = int(layer.get("height", 0))
    if not isinstance(rgba, list) or width <= 0 or height <= 0:
        raise ValueError("preview layer has neither PNG nor resolved RGBA data")
    if width * height > MAX_PIXELS or len(rgba) != width * height * 4:
        raise ValueError("invalid preview RGBA layer")
    if any(not isinstance(v, int) or v < 0 or v > 255 for v in rgba):
        raise ValueError("invalid RGBA byte")
    return PILImage.frombytes("RGBA", (width, height), bytes(rgba))


def _png_bytes_from_result(result: dict[str, Any]) -> bytes:
    uri = result.get("png_data_uri")
    if isinstance(uri, str):
        return _decode_png_data_uri(uri)

    layers = result.get("layers")
    width = int(result.get("canvas_width", 0))
    height = int(result.get("canvas_height", 0))
    if not isinstance(layers, list) or width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError("invalid preview canvas")

    canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError("invalid preview layer")
        image = _layer_image(layer)
        x = int(layer.get("x", 0))
        y = int(layer.get("y", 0))
        # Use a canvas-sized temporary layer so negative/out-of-canvas cel positions crop safely.
        placed = PILImage.new("RGBA", canvas.size, (0, 0, 0, 0))
        placed.paste(image, (x, y), image)
        canvas = PILImage.alpha_composite(canvas, placed)

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    data = out.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("composed PNG exceeds safety limit")
    return data


def _scale_png(raw: bytes, scale: int) -> bytes:
    if scale == 1:
        return raw
    image = PILImage.open(io.BytesIO(raw)).convert("RGBA")
    image = image.resize(
        (image.width * scale, image.height * scale),
        PILImage.Resampling.NEAREST,
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class MCPServer:
    def __init__(self, proxy: LibrespriteProxy, server_name: str = "libresprite"):
        self.proxy = proxy
        self.mcp = FastMCP(server_name)
        self._setup_tools()

    def call(self, operation: str, **payload: Any) -> dict[str, Any]:
        return _unwrap(self.proxy.execute(operation, payload))

    def status(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "ok": True,
            "relay": "running",
            "connected": self.proxy.connected,
            "mode": self.proxy.config.mode,
            "protocol_version": 1,
            "bridge": self.proxy.bridge_info,
        }
        if self.proxy.connected:
            try:
                info["libresprite"] = self.call("health")
            except Exception as exc:
                info["ok"] = False
                info["error"] = str(exc)
        return info

    def _setup_tools(self) -> None:
        m = self.mcp

        @m.tool()
        def health_check() -> dict:
            """Check relay/LibreSprite connection state without modifying the sprite."""
            return self.status()

        @m.tool()
        def get_libresprite_status() -> dict:
            """Return bridge, LibreSprite version/platform hints, protocol version, and SAFE/DEV mode."""
            return self.status()

        @m.tool()
        def get_capabilities() -> dict:
            """Return capabilities and explicit unsupported features for the connected LibreSprite build."""
            if not self.proxy.connected:
                return {"ok": False, "error": {"code": "NOT_CONNECTED", "message": "LibreSprite bridge is not connected"}}
            return self.call("health")

        @m.tool()
        def get_sprite_info() -> dict:
            """Return active sprite dimensions, filename, color mode, layer count and frame state. Indices are 0-based."""
            return self.call("get_sprite_info")

        @m.tool()
        def get_active_frame() -> dict:
            """Return the active frame index, 0-based."""
            return self.call("get_active_frame")

        @m.tool()
        def set_active_frame(frame: int) -> dict:
            """Activate a 0-based frame without changing its pixels."""
            return self.call("set_active_frame", frame=frame)

        @m.tool()
        def get_frames() -> dict:
            """Return the frame count and 0-based frame indices."""
            return self.call("get_frames")

        @m.tool()
        def get_layers() -> dict:
            """Return layers from bottom to top with 0-based indices and basic metadata."""
            return self.call("get_layers")

        @m.tool()
        def add_frame() -> dict:
            """Create an empty frame after the current frame and verify frame count increased."""
            return self.call("add_frame")

        @m.tool()
        def insert_frame(frame: int) -> dict:
            """Create an empty frame after the supplied 0-based frame and verify frame count increased."""
            return self.call("insert_frame", frame=frame)

        @m.tool()
        def duplicate_frame(frame: int) -> dict:
            """Duplicate a 0-based frame into a following frame and verify frame count increased."""
            return self.call("duplicate_frame", frame=frame)

        @m.tool()
        def delete_frame(frame: int) -> dict:
            """Delete a 0-based frame and verify frame count decreased. Destructive."""
            return self.call("delete_frame", frame=frame)

        @m.tool()
        def get_frame_durations() -> dict:
            """Return timing data if supported. Current upstream builds report this explicitly unsupported."""
            return self.call("get_frame_durations")

        @m.tool()
        def set_frame_duration(frame: int, duration_ms: int) -> dict:
            """Set one frame duration only when the connected build exposes a verified non-interactive API."""
            return self.call("set_frame_duration", frame=frame, duration_ms=duration_ms)

        @m.tool()
        def set_frame_durations(durations_ms: list[int]) -> dict:
            """Set all durations only when the connected build exposes a verified non-interactive API."""
            return self.call("set_frame_durations", durations_ms=durations_ms)

        @m.tool()
        def create_layer(name: str) -> dict:
            """Create a top image layer with the given name and verify the layer count/name."""
            return self.call("create_layer", name=name)

        @m.tool()
        def delete_layer(layer: int) -> dict:
            """Delete the 0-based image layer and verify layer count decreased. Destructive."""
            return self.call("delete_layer", layer=layer)

        @m.tool()
        def rename_layer(layer: int, name: str) -> dict:
            """Rename a 0-based image layer."""
            return self.call("rename_layer", layer=layer, name=name)

        @m.tool()
        def set_layer_visibility(layer: int, visible: bool) -> dict:
            """Set visibility of a 0-based image layer."""
            return self.call("set_layer_visibility", layer=layer, visible=visible)

        @m.tool()
        def set_active_layer(layer: int) -> dict:
            """Activate a 0-based image layer through LibreSprite's layer-navigation command."""
            return self.call("set_active_layer", layer=layer)

        @m.tool()
        def get_cel(layer: int, frame: int) -> dict:
            """Return cel metadata for a 0-based layer/frame, including position and native image dimensions."""
            return self.call("get_cel", layer=layer, frame=frame)

        @m.tool()
        def copy_cel(source_layer: int, source_frame: int, target_layer: int, target_frame: int) -> dict:
            """Copy pixels and position between existing cels. Source/target use 0-based layer/frame indices."""
            return self.call(
                "copy_cel",
                source_layer=source_layer,
                source_frame=source_frame,
                target_layer=target_layer,
                target_frame=target_frame,
            )

        @m.tool()
        def move_cel(layer: int, frame: int, x: int, y: int) -> dict:
            """Set the canvas position of an existing cel in integer pixels."""
            return self.call("move_cel", layer=layer, frame=frame, x=x, y=y)

        @m.tool()
        def clear_cel(layer: int, frame: int) -> dict:
            """Remove/clear the cel at a 0-based layer/frame using LibreSprite's ClearCel command."""
            return self.call("clear_cel", layer=layer, frame=frame)

        @m.tool()
        def get_pixel(layer: int, frame: int, x: int, y: int) -> dict:
            """Read one cel-local pixel and return resolved RGBA; indexed sprites also return the palette index."""
            return self.call("get_pixel", layer=layer, frame=frame, x=x, y=y)

        @m.tool()
        def get_pixels(layer: int, frame: int, x: int, y: int, width: int, height: int) -> dict:
            """Read a cel-local rectangular region as resolved RGBA pixels. Maximum 262144 pixels."""
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise ValueError("invalid or oversized region")
            return self.call("get_pixels", layer=layer, frame=frame, x=x, y=y, width=width, height=height)

        @m.tool()
        def draw_pixels(layer: int, frame: int, pixels: list[dict]) -> dict:
            """Write up to 262144 cel-local pixels in one request. Each item is {x,y,r,g,b,a}."""
            if len(pixels) > MAX_PIXELS:
                raise ValueError("too many pixels")
            return self.call("draw_pixels", layer=layer, frame=frame, pixels=pixels)

        @m.tool()
        def get_region(layer: int, frame: int, x: int, y: int, width: int, height: int) -> dict:
            """Read a cel-local rectangular region as resolved RGBA pixels."""
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise ValueError("invalid or oversized region")
            return self.call("get_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height)

        @m.tool()
        def set_region(layer: int, frame: int, x: int, y: int, width: int, height: int, rgba: list[int]) -> dict:
            """Replace a cel-local region from flat RGBA bytes. Indexed colors must exactly exist in the palette."""
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise ValueError("invalid or oversized region")
            if len(rgba) != width * height * 4:
                raise ValueError("rgba length mismatch")
            return self.call("set_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height, rgba=rgba)

        @m.tool()
        def copy_region(layer: int, frame: int, x: int, y: int, width: int, height: int, dest_x: int, dest_y: int) -> dict:
            """Copy a rectangular native-pixel region within one existing cel."""
            return self.call(
                "copy_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height, dest_x=dest_x, dest_y=dest_y
            )

        @m.tool()
        def move_region(layer: int, frame: int, x: int, y: int, width: int, height: int, dest_x: int, dest_y: int) -> dict:
            """Move a rectangular native-pixel region within one cel. Indexed transparent clearing may be unsupported."""
            return self.call(
                "move_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height, dest_x=dest_x, dest_y=dest_y
            )

        @m.tool()
        def clear_region(layer: int, frame: int, x: int, y: int, width: int, height: int) -> dict:
            """Clear a rectangular region to transparent where the color mode exposes an unambiguous transparent value."""
            return self.call("clear_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height)

        @m.tool()
        def flip_region(layer: int, frame: int, x: int, y: int, width: int, height: int, horizontal: bool = True) -> dict:
            """Flip a native-pixel region horizontally or vertically with no interpolation."""
            return self.call(
                "flip_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height, horizontal=horizontal
            )

        @m.tool()
        def get_image_data(layer: int, frame: int) -> dict:
            """Return full cel image metadata plus native raw image bytes. These bytes are not always RGBA."""
            return self.call("get_image_data", layer=layer, frame=frame)

        @m.tool()
        def set_image_data(layer: int, frame: int, data: list[int]) -> dict:
            """Replace full cel native image bytes. Exact stride*height byte length is required."""
            if len(data) > MAX_PIXELS * 4:
                raise ValueError("image data too large")
            return self.call("set_image_data", layer=layer, frame=frame, data=data)

        @m.tool()
        def get_palette() -> dict:
            """Return active sprite palette entries as resolved RGBA values."""
            return self.call("get_palette")

        @m.tool()
        def set_palette_color(index: int, r: int, g: int, b: int, a: int = 255) -> dict:
            """Set one 0-based palette entry to exact RGBA components."""
            return self.call("set_palette_color", index=index, r=r, g=g, b=b, a=a)

        @m.tool()
        def get_tags() -> dict:
            """Return tags only if a verified non-interactive tag API exists; otherwise explicit UNSUPPORTED."""
            return self.call("get_tags")

        @m.tool()
        def create_tag(from_frame: int, to_frame: int, name: str) -> dict:
            """Create a frame tag only on builds with a verified non-interactive tag API."""
            return self.call("create_tag", from_frame=from_frame, to_frame=to_frame, name=name)

        @m.tool()
        def delete_tag(name: str) -> dict:
            """Delete a frame tag only on builds with a verified non-interactive tag API."""
            return self.call("delete_tag", name=name)

        @m.tool()
        def render_frame_preview(frame: int, scale: int = 4) -> Image:
            """Return a real PNG MCP image for a 0-based frame. Scaling is nearest-neighbour only."""
            if scale < 1 or scale > 16:
                raise ValueError("scale must be 1..16")
            raw = _png_bytes_from_result(self.call("render_frame_preview", frame=frame))
            return Image(data=_scale_png(raw, scale), format="png")

        @m.tool()
        def render_animation_preview(frames: list[int], scale: int = 4, columns: int = 4) -> Image:
            """Return a PNG contact sheet for 1..64 frame indices. Performs no artistic judgement."""
            if not frames or len(frames) > 64:
                raise ValueError("frames must contain 1..64 indices")
            if scale < 1 or scale > 16 or columns < 1 or columns > 16:
                raise ValueError("invalid scale/columns")

            images: list[tuple[int, PILImage.Image]] = []
            for frame in frames:
                raw = _png_bytes_from_result(self.call("render_frame_preview", frame=frame))
                raw = _scale_png(raw, scale)
                images.append((frame, PILImage.open(io.BytesIO(raw)).convert("RGBA")))

            cell_w = max(image.width for _, image in images)
            cell_h = max(image.height for _, image in images) + 18
            rows = math.ceil(len(images) / columns)
            if cell_w * columns * cell_h * rows > MAX_PIXELS * 64:
                raise ValueError("contact sheet too large")

            sheet = PILImage.new("RGBA", (cell_w * columns, cell_h * rows), (0, 0, 0, 0))
            draw = ImageDraw.Draw(sheet)
            for index, (frame, image) in enumerate(images):
                x = (index % columns) * cell_w
                y = (index // columns) * cell_h
                sheet.alpha_composite(image, (x, y + 18))
                draw.text((x + 2, y + 2), f"F{frame}", fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 255))

            out = io.BytesIO()
            sheet.save(out, format="PNG")
            return Image(data=out.getvalue(), format="png")

        @m.tool()
        def compare_frames(frame_a: int, frame_b: int) -> dict:
            """Mechanically compare rendered frames: changed-pixel count/percentage and change bounding box."""
            a = PILImage.open(io.BytesIO(_png_bytes_from_result(self.call("render_frame_preview", frame=frame_a)))).convert("RGBA")
            b = PILImage.open(io.BytesIO(_png_bytes_from_result(self.call("render_frame_preview", frame=frame_b)))).convert("RGBA")
            if a.size != b.size:
                return {"ok": True, "same_size": False, "size_a": list(a.size), "size_b": list(b.size)}

            difference = ImageChops.difference(a, b)
            bbox = difference.getbbox()
            changed = 0
            if bbox:
                pa = a.load()
                pb = b.load()
                for y in range(a.height):
                    for x in range(a.width):
                        if pa[x, y] != pb[x, y]:
                            changed += 1
            total = a.width * a.height
            return {
                "ok": True,
                "same_size": True,
                "changed_pixel_count": changed,
                "change_percent": changed / total * 100 if total else 0,
                "bounding_box": list(bbox) if bbox else None,
                "size": [a.width, a.height],
            }

        @m.tool()
        def get_sprite_bounds(frame: int) -> dict:
            """Return the non-transparent bounding box of the rendered 0-based frame."""
            image = PILImage.open(io.BytesIO(_png_bytes_from_result(self.call("render_frame_preview", frame=frame)))).convert("RGBA")
            bbox = image.getchannel("A").getbbox()
            return {"ok": True, "frame": frame, "bounds": list(bbox) if bbox else None, "size": [image.width, image.height]}

        @m.tool()
        def save_sprite() -> dict:
            """Save the current editable sprite to its current filename."""
            return self.call("save_sprite")

        @m.tool()
        def save_as(path: str) -> dict:
            """Save the editable sprite to a validated path, constrained by LIBRESPRITE_MCP_ALLOWED_ROOT when set."""
            safe_path = normalize_allowed_path(path, self.proxy.config.allowed_root)
            return self.call("save_as", path=safe_path)

        @m.tool()
        def open_sprite(path: str) -> dict:
            """Open an existing sprite from a validated path, constrained by LIBRESPRITE_MCP_ALLOWED_ROOT when set."""
            safe_path = normalize_allowed_path(path, self.proxy.config.allowed_root, must_exist=True)
            return self.call("open_sprite", path=safe_path)

        @m.tool()
        def export_png(path: str, frame: int | None = None) -> dict:
            """Export PNG to a validated path. Optionally activate a 0-based frame first."""
            safe_path = normalize_allowed_path(path, self.proxy.config.allowed_root)
            return self.call("export_png", path=safe_path, frame=frame)

        @m.tool()
        def export_gif(path: str) -> dict:
            """Export an animated GIF through LibreSprite's normal save-as-copy path."""
            safe_path = normalize_allowed_path(path, self.proxy.config.allowed_root)
            return self.call("export_gif", path=safe_path)

        @m.tool()
        def export_spritesheet(path: str) -> dict:
            """Export a spritesheet only when a verified non-interactive API exists; otherwise explicit UNSUPPORTED."""
            safe_path = normalize_allowed_path(path, self.proxy.config.allowed_root)
            return self.call("export_spritesheet", path=safe_path)

        if self.proxy.config.mode == "dev":

            @m.tool()
            def run_script(script: str) -> dict:
                """UNSAFE DEV-ONLY: execute arbitrary LibreSprite JavaScript. Never registered in SAFE mode."""
                return self.call("run_script", script=script)

    def run(self, transport: str = "stdio") -> None:
        self.mcp.run(transport=transport)
