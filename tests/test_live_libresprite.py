import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LIBRESPRITE_MCP_LIVE") != "1",
    reason="requires an interactive LibreSprite session with remote/mcp.js connected",
)

def test_live_environment_marker():
    # Live end-to-end execution is driven through the MCP client because the bridge
    # must pair with the exact relay instance launched by the client.
    # This marker prevents CI from claiming a live LibreSprite test occurred.
    assert os.environ["LIBRESPRITE_MCP_LIVE"] == "1"
