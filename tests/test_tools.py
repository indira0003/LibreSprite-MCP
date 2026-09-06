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
