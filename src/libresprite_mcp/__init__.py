from __future__ import annotations
import argparse, logging, os
from .protocol import RelayConfig, DEFAULT_PORT, DEFAULT_TIMEOUT
from .libresprite_proxy import LibrespriteProxy
from .mcp_server import MCPServer

def main() -> None:
    parser = argparse.ArgumentParser(prog="libresprite-mcp")
    parser.add_argument("--mode", choices=["safe","dev"], default=os.environ.get("LIBRESPRITE_MCP_MODE","safe"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LIBRESPRITE_MCP_PORT", DEFAULT_PORT)))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("LIBRESPRITE_MCP_TIMEOUT", DEFAULT_TIMEOUT)))
    parser.add_argument("--allowed-root", default=os.environ.get("LIBRESPRITE_MCP_ALLOWED_ROOT"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    config = RelayConfig(port=args.port, timeout=args.timeout, mode=args.mode, allowed_root=args.allowed_root)
    proxy = LibrespriteProxy(config)
    proxy.start()
    try:
        MCPServer(proxy).run("stdio")
    finally:
        proxy.stop()

if __name__ == "__main__":
    main()
