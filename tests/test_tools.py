from libresprite_mcp.libresprite_proxy import LibrespriteProxy
from libresprite_mcp.mcp_server import MCPServer
from libresprite_mcp.protocol import RelayConfig


def tool_names(mode: str) -> set[str]:
    proxy = LibrespriteProxy(RelayConfig(port=64831 if mode == "safe" else 64832, mode=mode))
    server = MCPServer(proxy)
    return {tool.name for tool in server.mcp._tool_manager.list_tools()}


def test_safe_mode_never_registers_run_script():
    names = tool_names("safe")
    assert "run_script" not in names
    assert "render_frame_preview" in names
    assert "render_animation_preview" in names
    assert "draw_pixels" in names
    assert "duplicate_frame" in names


def test_dev_mode_registers_run_script_explicitly():
    names = tool_names("dev")
    assert "run_script" in names


def test_tool_schemas_are_generated():
    proxy = LibrespriteProxy(RelayConfig(port=64833, mode="safe"))
    server = MCPServer(proxy)
    tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
    schema = tools["draw_pixels"].parameters
    assert schema["type"] == "object"
    assert "pixels" in schema["properties"]
    assert "layer" in schema["properties"]
    assert "frame" in schema["properties"]


def test_compare_detects_rgb_changes_with_identical_alpha():
    import asyncio
    server = MCPServer(LibrespriteProxy(RelayConfig()))
    def call(operation, **payload):
        color = [255, 0, 0, 255] if payload["frame"] == 0 else [0, 255, 0, 255]
        return {"canvas_width": 1, "canvas_height": 1, "layers": [
            {"x": 0, "y": 0, "width": 1, "height": 1, "rgba": color}]}
    server.call = call
    tool = server.mcp._tool_manager.get_tool("compare_frames")
    result = asyncio.run(tool.run({"frame_a": 0, "frame_b": 1}, convert_result=False))
    assert result["changed_pixel_count"] == 1
