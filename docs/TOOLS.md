# MCP tools

All frame and layer indices are **0-based**. Pixel coordinates use `x=0` at the left and `y=0` at the top. SAFE mode is the default.

## Connection
- `health_check()` — relay/bridge state, protocol version, SAFE/DEV mode.
- `get_libresprite_status()` — same status-oriented view.
- `get_capabilities()` — capabilities and explicit unsupported features.

## Sprite/files
- `get_sprite_info()`
- `open_sprite(path)`
- `save_sprite()`
- `save_as(path)`
- `export_png(path, frame=None)`
- `export_gif(path)`
- `export_spritesheet(path)` — currently reports unsupported unless a verified non-interactive upstream route is added.

When `LIBRESPRITE_MCP_ALLOWED_ROOT` is set, file paths must resolve beneath that directory.

## Frames/state
- `get_active_frame()`
- `set_active_frame(frame)`
- `get_frames()`
- `add_frame()` — adds an empty frame after the active frame.
- `insert_frame(frame)` — activates `frame`, then adds an empty frame after it.
- `duplicate_frame(frame)` — uses LibreSprite `NewFrame(content=frame)` and verifies frame count.
- `delete_frame(frame)`

## Timing
- `get_frame_durations()`
- `set_frame_duration(frame, duration_ms)`
- `set_frame_durations(durations_ms)`

These currently return an explicit unsupported result. Upstream `FrameProperties` is a UI-only command and no verified non-interactive setter/getter is exposed by the current scripting surface.

## Layers
- `get_layers()`
- `create_layer(name)`
- `delete_layer(layer)`
- `rename_layer(layer, name)`
- `set_layer_visibility(layer, visible)`
- `set_active_layer(layer)`

## Cels
- `get_cel(layer, frame)`
- `copy_cel(source_layer, source_frame, target_layer, target_frame)`
- `move_cel(layer, frame, x, y)`
- `clear_cel(layer, frame)`

## Pixels/regions
- `get_pixel(layer, frame, x, y)`
- `get_pixels(layer, frame, x, y, width, height)`
- `draw_pixels(layer, frame, pixels)` — each pixel is `{x,y,r,g,b,a}`; maximum 262,144 entries.
- `get_region(...)`
- `set_region(layer, frame, x, y, width, height, rgba)` — flat RGBA byte list.
- `copy_region(...)`
- `move_region(...)`
- `clear_region(...)`
- `flip_region(..., horizontal=True)`
- `get_image_data(layer, frame)`
- `set_image_data(layer, frame, rgba)`

No interpolation or antialiasing is introduced by these pixel operations.

## Palette/tags
- `get_palette()`
- `set_palette_color(index, r, g, b, a=255)`
- `get_tags()`
- `create_tag(from_frame, to_frame, name)`
- `delete_tag(name)`

Tag operations currently return explicit unsupported results because the verified scripting surface does not expose a non-interactive frame-tag object API.

## Vision/inspection
- `render_frame_preview(frame, scale=4)` — returns **MCP ImageContent** (PNG), composed from visible cels and scaled nearest-neighbour in Python.
- `render_animation_preview(frames, scale=4, columns=4)` — returns a contact-sheet PNG as real MCP image content.
- `compare_frames(frame_a, frame_b)` — mechanical diff only: changed pixels, percentage and bounding box.
- `get_sprite_bounds(frame)` — non-transparent bounds.

These tools do not judge pose quality, arcs, weight, acting, silhouette quality or other artistic concepts.

## DEV only
- `run_script(script)`

`run_script` is not registered at all in SAFE mode. In DEV mode it intentionally enables arbitrary LibreSprite JavaScript and must not be used for the Astra evaluation.
