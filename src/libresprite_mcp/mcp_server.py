from __future__ import annotations
import base64, io, math
from typing import Any
from PIL import Image as PILImage, ImageChops, ImageDraw
from mcp.server.fastmcp import FastMCP, Image
from .libresprite_proxy import LibrespriteProxy
from .protocol import MAX_PIXELS, normalize_allowed_path

def _unwrap(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is False:
        return response
    return response.get("result", response)

def _png_bytes_from_result(result: dict[str, Any]) -> bytes:
    uri = result.get("png_data_uri") or result.get("data")
    if isinstance(uri, str):
        if uri.startswith("data:image/png;base64,"):
            uri = uri.split(",", 1)[1]
        data = base64.b64decode(uri, validate=True)
        if len(data) > 8_000_000:
            raise ValueError("PNG result exceeds safety limit")
        return data
    layers = result.get("layers")
    if isinstance(layers, list):
        w = int(result.get("canvas_width", 0)); h = int(result.get("canvas_height", 0))
        if w <= 0 or h <= 0 or w*h > MAX_PIXELS:
            raise ValueError("invalid preview canvas")
        canvas = PILImage.new("RGBA", (w, h), (0,0,0,0))
        for layer in layers:
            luri = layer.get("png_data_uri")
            if not isinstance(luri, str):
                continue
            if luri.startswith("data:image/png;base64,"):
                luri = luri.split(",", 1)[1]
            raw = base64.b64decode(luri, validate=True)
            im = PILImage.open(io.BytesIO(raw)).convert("RGBA")
            canvas.alpha_composite(im, (int(layer.get("x",0)), int(layer.get("y",0))))
        out = io.BytesIO(); canvas.save(out, format="PNG")
        return out.getvalue()
    raise RuntimeError("bridge did not return PNG data")

class MCPServer:
    def __init__(self, proxy: LibrespriteProxy, server_name: str = "libresprite"):
        self.proxy = proxy
        self.mcp = FastMCP(server_name)
        self._setup_tools()

    def call(self, operation: str, **payload):
        return _unwrap(self.proxy.execute(operation, payload))

    def _setup_tools(self):
        m = self.mcp

        @m.tool()
        def health_check() -> dict:
            """Check relay and LibreSprite bridge state. Does not modify the sprite."""
            info = {"ok": True,"relay": "running","connected": self.proxy.connected,"mode": self.proxy.config.mode,
                    "protocol_version": 1,"bridge": self.proxy.bridge_info}
            if self.proxy.connected:
                try: info["libresprite"] = self.call("health")
                except Exception as exc: info["ok"] = False; info["error"] = str(exc)
            return info

        @m.tool()
        def get_libresprite_status() -> dict:
            """Return connection, bridge version, LibreSprite version/build hints, and SAFE/DEV mode."""
            return health_check()

        @m.tool()
        def get_capabilities() -> dict:
            """Return operations advertised by the connected bridge and known unsupported operations."""
            if not self.proxy.connected:
                return {"ok":False,"error":{"code":"NOT_CONNECTED","message":"LibreSprite bridge is not connected"}}
            return self.call("health")

        @m.tool()
        def get_sprite_info() -> dict:
            """Return active sprite dimensions, filename, color mode, layer count, active frame and active layer. Frames are 0-based."""
            return self.call("get_sprite_info")

        @m.tool()
        def get_active_frame() -> dict:
            """Return the active frame index, 0-based."""
            return self.call("get_active_frame")

        @m.tool()
        def set_active_frame(frame: int) -> dict:
            """Activate a frame by 0-based index. Does not alter pixels."""
            return self.call("set_active_frame", frame=frame)

        @m.tool()
        def get_frames() -> dict:
            """Return known frame indices and count. Frame indices are 0-based."""
            return self.call("get_frames")

        @m.tool()
        def get_layers() -> dict:
            """Return image layers from bottom to top with 0-based layer indices."""
            return self.call("get_layers")

        @m.tool()
        def add_frame() -> dict:
            """Create a new empty frame after the active frame using LibreSprite's NewFrame command."""
            return self.call("add_frame")

        @m.tool()
        def insert_frame(frame: int) -> dict:
            """Activate the 0-based frame and insert a new empty frame after it."""
            return self.call("insert_frame", frame=frame)

        @m.tool()
        def duplicate_frame(frame: int) -> dict:
            """Duplicate a 0-based frame into a newly created following frame."""
            return self.call("duplicate_frame", frame=frame)

        @m.tool()
        def delete_frame(frame: int) -> dict:
            """Delete a 0-based frame. This is destructive and changes subsequent frame indices."""
            return self.call("delete_frame", frame=frame)

        @m.tool()
        def get_frame_durations() -> dict:
            """Return frame timing capability; unsupported on current upstream scripting when no non-interactive API exists."""
            return self.call("get_frame_durations")

        @m.tool()
        def set_frame_duration(frame: int, duration_ms: int) -> dict:
            """Set one frame duration if supported by the connected LibreSprite build; otherwise returns UNSUPPORTED."""
            return self.call("set_frame_duration", frame=frame, duration_ms=duration_ms)

        @m.tool()
        def set_frame_durations(durations_ms: list[int]) -> dict:
            """Set all frame durations if supported; otherwise returns UNSUPPORTED."""
            return self.call("set_frame_durations", durations_ms=durations_ms)

        @m.tool()
        def create_layer(name: str) -> dict:
            """Create an image layer and verify the layer count increased."""
            return self.call("create_layer", name=name)

        @m.tool()
        def delete_layer(layer: int) -> dict:
            """Delete the 0-based layer index. Destructive."""
            return self.call("delete_layer", layer=layer)

        @m.tool()
        def rename_layer(layer: int, name: str) -> dict:
            """Rename the 0-based layer."""
            return self.call("rename_layer", layer=layer, name=name)

        @m.tool()
        def set_layer_visibility(layer: int, visible: bool) -> dict:
            """Set visibility of the 0-based layer."""
            return self.call("set_layer_visibility", layer=layer, visible=visible)

        @m.tool()
        def set_active_layer(layer: int) -> dict:
            """Activate the 0-based layer."""
            return self.call("set_active_layer", layer=layer)

        @m.tool()
        def get_cel(layer: int, frame: int) -> dict:
            """Return cel metadata for a 0-based layer/frame, including position and image dimensions."""
            return self.call("get_cel", layer=layer, frame=frame)

        @m.tool()
        def copy_cel(source_layer: int, source_frame: int, target_layer: int, target_frame: int) -> dict:
            """Copy a cel between 0-based layer/frame coordinates."""
            return self.call("copy_cel", source_layer=source_layer, source_frame=source_frame,
                             target_layer=target_layer, target_frame=target_frame)

        @m.tool()
        def move_cel(layer: int, frame: int, x: int, y: int) -> dict:
            """Set cel position in canvas pixels for a 0-based layer/frame."""
            return self.call("move_cel", layer=layer, frame=frame, x=x, y=y)

        @m.tool()
        def clear_cel(layer: int, frame: int) -> dict:
            """Clear the cel at a 0-based layer/frame."""
            return self.call("clear_cel", layer=layer, frame=frame)

        @m.tool()
        def get_pixel(layer: int, frame: int, x: int, y: int) -> dict:
            """Read one pixel at cel-local x/y coordinates."""
            return self.call("get_pixel", layer=layer, frame=frame, x=x, y=y)

        @m.tool()
        def get_pixels(layer: int, frame: int, x: int, y: int, width: int, height: int) -> dict:
            """Read a rectangular cel-local pixel region. RGBA values are returned as integer components."""
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS: raise ValueError("invalid or oversized region")
            return self.call("get_pixels", layer=layer, frame=frame, x=x, y=y, width=width, height=height)

        @m.tool()
        def draw_pixels(layer: int, frame: int, pixels: list[dict]) -> dict:
            """Write many cel-local pixels atomically. Each item: {x,y,r,g,b,a}; max 262144 pixels."""
            if len(pixels) > MAX_PIXELS: raise ValueError("too many pixels")
            return self.call("draw_pixels", layer=layer, frame=frame, pixels=pixels)

        @m.tool()
        def get_region(layer: int, frame: int, x: int, y: int, width: int, height: int) -> dict:
            """Read a cel-local rectangular region."""
            return get_pixels(layer, frame, x, y, width, height)

        @m.tool()
        def set_region(layer: int, frame: int, x: int, y: int, width: int, height: int, rgba: list[int]) -> dict:
            """Replace a cel-local region. rgba is flat RGBA bytes of exactly width*height*4 values."""
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS: raise ValueError("invalid or oversized region")
            if len(rgba) != width * height * 4: raise ValueError("rgba length mismatch")
            return self.call("set_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height, rgba=rgba)

        @m.tool()
        def copy_region(layer: int, frame: int, x: int, y: int, width: int, height: int, dest_x: int, dest_y: int) -> dict:
            """Copy a rectangular region within one cel."""
            return self.call("copy_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height,dest_x=dest_x,dest_y=dest_y)

        @m.tool()
        def move_region(layer: int, frame: int, x: int, y: int, width: int, height: int, dest_x: int, dest_y: int) -> dict:
            """Move a rectangular region within one cel, clearing the source."""
            return self.call("move_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height,dest_x=dest_x,dest_y=dest_y)

        @m.tool()
        def clear_region(layer: int, frame: int, x: int, y: int, width: int, height: int) -> dict:
            """Clear a rectangular cel-local region to transparent."""
            return self.call("clear_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height)

        @m.tool()
        def flip_region(layer: int, frame: int, x: int, y: int, width: int, height: int, horizontal: bool = True) -> dict:
            """Flip a rectangular region horizontally or vertically without interpolation."""
            return self.call("flip_region", layer=layer, frame=frame, x=x, y=y, width=width, height=height,horizontal=horizontal)

        @m.tool()
        def get_image_data(layer: int, frame: int) -> dict:
            """Return full cel image metadata and native image bytes. Intended for small/medium pixel-art images."""
            return self.call("get_image_data", layer=layer, frame=frame)

        @m.tool()
        def set_image_data(layer: int, frame: int, rgba: list[int]) -> dict:
            """Replace full cel image bytes. Exact image byte length required by LibreSprite."""
            if len(rgba) > MAX_PIXELS * 4: raise ValueError("image data too large")
            return self.call("set_image_data", layer=layer, frame=frame, rgba=rgba)

        @m.tool()
        def get_palette() -> dict:
            """Return active sprite palette entries as RGBA."""
            return self.call("get_palette")

        @m.tool()
        def set_palette_color(index: int, r: int, g: int, b: int, a: int = 255) -> dict:
            """Set one palette entry by 0-based index."""
            return self.call("set_palette_color", index=index, r=r, g=g, b=b, a=a)

        @m.tool()
        def get_tags() -> dict:
            """Return frame-tag capability and tags if exposed by this LibreSprite build."""
            return self.call("get_tags")

        @m.tool()
        def create_tag(from_frame: int, to_frame: int, name: str) -> dict:
            """Create an animation tag when the scripting build supports non-interactive tag creation."""
            return self.call("create_tag", from_frame=from_frame, to_frame=to_frame, name=name)

        @m.tool()
        def delete_tag(name: str) -> dict:
            """Delete an animation tag by name when supported."""
            return self.call("delete_tag", name=name)

        @m.tool()
        def render_frame_preview(frame: int, scale: int = 4) -> Image:
            """Return the requested 0-based frame as real PNG image content. Nearest-neighbour scaling only."""
            if scale < 1 or scale > 16: raise ValueError("scale must be 1..16")
            raw = _png_bytes_from_result(self.call("render_frame_preview", frame=frame))
            if scale != 1:
                img = PILImage.open(io.BytesIO(raw)).convert("RGBA")
                img = img.resize((img.width * scale, img.height * scale), PILImage.Resampling.NEAREST)
                out = io.BytesIO(); img.save(out, format="PNG"); raw = out.getvalue()
            return Image(data=raw, format="png")

        @m.tool()
        def render_animation_preview(frames: list[int], scale: int = 4, columns: int = 4) -> Image:
            """Return a contact-sheet PNG for 0-based frame indices. No artistic analysis is performed."""
            if not frames or len(frames) > 64: raise ValueError("frames must contain 1..64 indices")
            if scale < 1 or scale > 16 or columns < 1 or columns > 16: raise ValueError("invalid scale/columns")
            imgs=[]
            for f in frames:
                raw=_png_bytes_from_result(self.call("render_frame_preview", frame=f))
                im=PILImage.open(io.BytesIO(raw)).convert("RGBA")
                if scale != 1: im=im.resize((im.width*scale,im.height*scale),PILImage.Resampling.NEAREST)
                imgs.append((f,im))
            cell_w=max(im.width for _,im in imgs); cell_h=max(im.height for _,im in imgs)+18; rows=math.ceil(len(imgs)/columns)
            sheet=PILImage.new("RGBA",(cell_w*columns,cell_h*rows),(0,0,0,0)); draw=ImageDraw.Draw(sheet)
            for i,(f,im) in enumerate(imgs):
                cx=(i%columns)*cell_w; cy=(i//columns)*cell_h; sheet.alpha_composite(im,(cx,cy+18))
                draw.text((cx+2,cy+2),f"F{f}",fill=(255,255,255,255),stroke_width=1,stroke_fill=(0,0,0,255))
            out=io.BytesIO(); sheet.save(out,format="PNG"); return Image(data=out.getvalue(),format="png")

        @m.tool()
        def compare_frames(frame_a: int, frame_b: int) -> dict:
            """Mechanically compare rendered frames: changed pixel count, percentage, and change bounding box."""
            a=PILImage.open(io.BytesIO(_png_bytes_from_result(self.call("render_frame_preview",frame=frame_a)))).convert("RGBA")
            b=PILImage.open(io.BytesIO(_png_bytes_from_result(self.call("render_frame_preview",frame=frame_b)))).convert("RGBA")
            if a.size!=b.size: return {"ok":True,"same_size":False,"size_a":a.size,"size_b":b.size}
            bbox=ImageChops.difference(a,b).getbbox(); changed=0
            if bbox:
                pa=a.load(); pb=b.load()
                for y in range(a.height):
                    for x in range(a.width):
                        if pa[x,y]!=pb[x,y]: changed+=1
            total=a.width*a.height
            return {"ok":True,"same_size":True,"changed_pixel_count":changed,"change_percent":changed/total*100 if total else 0,
                    "bounding_box":list(bbox) if bbox else None,"size":[a.width,a.height]}

        @m.tool()
        def get_sprite_bounds(frame: int) -> dict:
            """Return non-transparent bounds of the rendered 0-based frame."""
            im=PILImage.open(io.BytesIO(_png_bytes_from_result(self.call("render_frame_preview",frame=frame)))).convert("RGBA")
            bbox=im.getchannel("A").getbbox(); return {"ok":True,"frame":frame,"bounds":list(bbox) if bbox else None,"size":[im.width,im.height]}

        @m.tool()
        def save_sprite() -> dict:
            """Save the active editable sprite to its current filename."""
            return self.call("save_sprite")

        @m.tool()
        def save_as(path: str) -> dict:
            """Save the active sprite to a validated path within LIBRESPRITE_MCP_ALLOWED_ROOT when configured."""
            return self.call("save_as", path=normalize_allowed_path(path,self.proxy.config.allowed_root))

        @m.tool()
        def open_sprite(path: str) -> dict:
            """Open a sprite from a validated path within LIBRESPRITE_MCP_ALLOWED_ROOT when configured."""
            return self.call("open_sprite", path=normalize_allowed_path(path,self.proxy.config.allowed_root))

        @m.tool()
        def export_png(path: str, frame: int | None = None) -> dict:
            """Export PNG to a validated path. Optionally selects a 0-based frame first."""
            return self.call("export_png",path=normalize_allowed_path(path,self.proxy.config.allowed_root),frame=frame)

        @m.tool()
        def export_gif(path: str) -> dict:
            """Export an animated GIF when supported by LibreSprite's normal save/export path."""
            return self.call("export_gif",path=normalize_allowed_path(path,self.proxy.config.allowed_root))

        @m.tool()
        def export_spritesheet(path: str) -> dict:
            """Export a spritesheet when non-interactive export is supported; otherwise returns UNSUPPORTED."""
            return self.call("export_spritesheet",path=normalize_allowed_path(path,self.proxy.config.allowed_root))

        if self.proxy.config.mode == "dev":
            @m.tool()
            def run_script(script: str) -> dict:
                """UNSAFE DEV-ONLY: execute arbitrary LibreSprite JavaScript. Never available in SAFE mode."""
                return self.call("run_script",script=script)

    def run(self, transport: str = "stdio"):
        self.mcp.run(transport=transport)
