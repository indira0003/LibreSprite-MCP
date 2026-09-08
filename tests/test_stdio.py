"""Exercise MCP initialization and tool calls over real subprocess STDIO."""
import asyncio
import json
import socket
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def parameters(port):
    return StdioServerParameters(command=sys.executable, args=[
        "-c", "from libresprite_mcp import main; main()", "--port", str(port), "--mode", "safe"])


def text_result(response):
    assert not response.isError, response
    return json.loads(next(block.text for block in response.content if block.type == "text"))


def test_real_stdio_initializes_and_reports_disconnected_without_noise():
    async def run():
        async with stdio_client(parameters(available_port())) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert "draw_pixels" in names and "run_script" not in names
                status = text_result(await session.call_tool("health_check", {}))
                assert status["mode"] == "safe" and status["connected"] is False
                caps = text_result(await session.call_tool("get_capabilities", {}))
                assert caps["error"]["code"] == "NOT_CONNECTED"
    asyncio.run(run())
