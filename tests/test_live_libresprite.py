"""Opt-in REAL executable test. No marker-only pass and no mocked editor API.

Set LIBRESPRITE_MCP_LIVE_EXE to the LibreSprite executable. The test creates
its own PNG, document, script instance and ephemeral loopback relay. It never
opens a user sprite and terminates only the process it launched.
"""
import io
import os
from pathlib import Path
import subprocess
import time

from flask import request
from PIL import Image
import pytest

from libresprite_mcp.libresprite_proxy import LibrespriteProxy
from libresprite_mcp.mcp_server import MCPServer, _png_bytes_from_result
from libresprite_mcp.protocol import RelayConfig

EXE = os.environ.get("LIBRESPRITE_MCP_LIVE_EXE")
pytestmark = pytest.mark.skipif(not EXE, reason="requires LIBRESPRITE_MCP_LIVE_EXE and a desktop session")


def until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("live LibreSprite condition timed out")


def test_real_pixels_frames_previews_and_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRESPRITE_MCP_EXECUTABLE", EXE)
    proxy = LibrespriteProxy(RelayConfig(port=0, timeout=8, lease_seconds=10))
    faults = {"/next": 0, "/result": 0}
    @proxy.app.before_request
    def inject_transient_failure():
        if faults.get(request.path, 0):
            faults[request.path] -= 1
            return {"ok": False, "error": {"code": "TEST_TRANSIENT"}}, 503
    proxy.start()
    port = proxy._server.server_port
    source = (Path(__file__).parents[1] / "remote/mcp.js").read_text(encoding="utf-8")
    source = source.replace("127.0.0.1:64823", f"127.0.0.1:{port}")
    source += '\nconst testEvent = onEvent; onEvent = function(e) { testEvent(e); if(e === "init") testEvent("connect_click"); };\n'
    script = tmp_path / "live-bridge.js"
    script.write_text(source, encoding="utf-8")
    fixture = tmp_path / "fixture.png"
    Image.new("RGBA", (8, 8), (20, 30, 40, 255)).save(fixture)
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    with (tmp_path / "libresprite.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([EXE, str(fixture), "--script", str(script)],
                                   stdout=log, stderr=log, startupinfo=startup)
        try:
            until(lambda: proxy.connected)
            def call(op, **payload):
                response = proxy.execute(op, payload)
                assert response["ok"], response
                return response["result"]
            assert call("health")["capabilities"]["arbitrary_script"] is False
            info = call("get_sprite_info")
            assert (info["width"], info["height"], info["frame_count"]) == (8, 8, 1)
            assert call("draw_pixels", layer=0, frame=0,
                        pixels=[{"x": 0, "y": 0, "r": 255, "g": 0, "b": 0, "a": 255}])["written"] == 1
            assert call("get_pixel", layer=0, frame=0, x=0, y=0)["r"] == 255
            faults["/result"] = 1
            assert call("duplicate_frame", frame=0)["after"] == 2
            assert call("get_frames")["count"] == 2  # retry did not duplicate twice
            faults["/next"] = 1
            call("draw_pixels", layer=0, frame=1,
                 pixels=[{"x": 0, "y": 0, "r": 0, "g": 255, "b": 0, "a": 255}])
            raw = _png_bytes_from_result(call("render_frame_preview", frame=1))
            preview = Image.open(io.BytesIO(raw))
            assert preview.getpixel((0, 0)) == (0, 255, 0, 255)
            assert call("get_pixel", layer=0, frame=0, x=0, y=0)["r"] == 255
            preview.save(tmp_path / "verified-preview.png")
            assert call("add_frame")["after"] == 3
            assert call("delete_frame", frame=2)["after"] == 2
            call("save_as", path=str(tmp_path / "verified.aseprite"))
            assert call("create_layer", name="Temporary test")["layer"] == 1
            call("set_layer_visibility", layer=1, visible=False)
            assert proxy.execute("delete_layer", {"layer": 1})["error"]["code"] == "HIDDEN_LAYER_MUST_BE_VISIBLE"
            call("set_layer_visibility", layer=1, visible=True)
            assert call("delete_layer", layer=1)["layer_count"] == 1
            before_export = call("get_sprite_info")
            server = MCPServer(proxy)
            assert server.export(str(tmp_path / "verified.gif"), "gif")["exported"]
            with Image.open(tmp_path / "verified.gif") as gif:
                assert gif.n_frames == 2
                assert gif.info["duration"] == 100
                assert gif.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
                gif.seek(1)
                assert gif.info["duration"] == 100
                assert gif.convert("RGB").getpixel((0, 0)) == (0, 255, 0)
            assert server.export(str(tmp_path / "verified.png"), "png", 1)["exported"]
            with Image.open(tmp_path / "verified.png") as png:
                assert png.convert("RGBA").getpixel((0, 0)) == (0, 255, 0, 255)
            assert call("get_sprite_info") == before_export
            assert not list(tmp_path.glob(".libresprite-export-*"))
            # Real HTTP 0: stop the socket listener long enough for a poll to
            # fail, then restart the relay with a new token and empty session.
            old_token = proxy.session_token
            proxy.stop()
            time.sleep(2)
            proxy.config = RelayConfig(port=port, timeout=8, lease_seconds=10)
            proxy.start()
            until(lambda: proxy.connected)
            assert proxy.session_token != old_token
            assert call("get_frames")["count"] == 2
            # Idle stability covers multiple authenticated polls after recovery.
            time.sleep(3)
            assert proxy.connected
            assert call("health")["connected"]
            # Abrupt editor exit, like closing/reopening LibreSprite: a new
            # script must recover the stale lease without ALREADY_PAIRED lockout.
            old_token = proxy.session_token
            process.terminate()
            process.wait(timeout=10)
            process = subprocess.Popen([EXE, str(tmp_path / "verified.aseprite"), "--script", str(script)],
                                       stdout=log, stderr=log, startupinfo=startup)
            until(lambda: proxy.connected and proxy.session_token != old_token, timeout=20)
            assert call("get_frames")["count"] == 2
            assert call("get_pixel", layer=0, frame=1, x=0, y=0)["g"] == 255
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            proxy.stop()


def test_real_stdio_to_libresprite(tmp_path):
    import asyncio
    import base64
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from test_stdio import available_port, parameters, text_result

    async def run():
        port = available_port()
        source = (Path(__file__).parents[1] / "remote/mcp.js").read_text(encoding="utf-8")
        source = source.replace("127.0.0.1:64823", f"127.0.0.1:{port}")
        source += '\nconst testEvent = onEvent; onEvent = function(e) { testEvent(e); if(e === "init") testEvent("connect_click"); };\n'
        script = tmp_path / "stdio-bridge.js"
        script.write_text(source, encoding="utf-8")
        fixture = tmp_path / "stdio-fixture.png"
        Image.new("RGBA", (8, 8), (20, 30, 40, 255)).save(fixture)
        startup = None
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
        params = parameters(port)
        params.env = {**os.environ, "LIBRESPRITE_MCP_EXECUTABLE": EXE}
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                with (tmp_path / "libresprite.log").open("w", encoding="utf-8") as log:
                    process = subprocess.Popen([EXE, str(fixture), "--script", str(script)],
                                               stdout=log, stderr=log, startupinfo=startup)
                    try:
                        async def call(name, **payload):
                            return text_result(await session.call_tool(name, payload))
                        deadline = time.monotonic() + 15
                        while not (await call("health_check"))["connected"]:
                            assert time.monotonic() < deadline
                            await asyncio.sleep(0.1)
                        assert (await call("get_sprite_info"))["frame_count"] == 1
                        assert (await call("draw_pixels", layer=0, frame=0,
                            pixels=[{"x": 0, "y": 0, "r": 255, "g": 0, "b": 0, "a": 255}]))["written"] == 1
                        assert (await call("duplicate_frame", frame=0))["after"] == 2
                        await call("draw_pixels", layer=0, frame=1,
                            pixels=[{"x": 0, "y": 0, "r": 0, "g": 255, "b": 0, "a": 255}])
                        response = await session.call_tool("render_animation_preview", {"frames": [0, 1], "scale": 1})
                        assert not response.isError
                        block = next(block for block in response.content if block.type == "image")
                        raw = base64.b64decode(block.data)
                        image = Image.open(io.BytesIO(raw))
                        assert image.getpixel((0, 18)) == (255, 0, 0, 255)
                        image.save(tmp_path / "stdio-contact-sheet.png")
                        assert (await call("get_capabilities"))["capabilities"]["arbitrary_script"] is False
                        assert (await call("export_gif", path=str(tmp_path / "stdio.gif")))["exported"]
                        assert (await call("export_png", path=str(tmp_path / "stdio.png"), frame=1))["exported"]
                        with Image.open(tmp_path / "stdio.gif") as gif:
                            assert gif.n_frames == 2
                    finally:
                        if process.poll() is None:
                            process.terminate()
                        process.wait(timeout=10)
    asyncio.run(run())
