# Architecture

LibreSprite does not provide an Aseprite-compatible headless CLI. This fork therefore controls a **live LibreSprite session** through its JavaScript scripting API and `storage.fetch`.

```text
MCP client / Astra
       |
       | stdio MCP
       v
Python FastMCP server
       |
       | typed operation + request_id
       v
127.0.0.1 relay
       |
       | session-token authenticated polling
       v
remote/mcp.js inside LibreSprite
       |
       | explicit SAFE dispatcher
       v
LibreSprite scripting API
```

## SAFE mode

SAFE mode is the default. The Python side accepts only names in `SAFE_OPERATIONS`. The JavaScript side contains a second explicit `switch` dispatcher. The wire protocol does **not** carry arbitrary JavaScript in SAFE mode.

A request contains:

```json
{
  "protocol_version": 1,
  "request_id": "uuid",
  "operation": "draw_pixels",
  "payload": {}
}
```

Responses repeat the request ID and contain either a structured result or structured error. Unknown/stale IDs are rejected. Requests have finite timeouts and a bounded queue.

## Pairing

The relay binds to `127.0.0.1`. LibreSprite performs one-time loopback pairing and receives a cryptographically random session token. Subsequent `/next` and `/result` traffic sends that token in the `X-LibreSprite-Token` header. The token is deliberately not placed in URLs/query strings.

This is a local TOFU design, not a claim that a compromised local machine can be made trustworthy. SAFE mode still restricts the paired bridge to its operation whitelist.

## DEV mode

`--mode dev` explicitly registers `run_script` and permits arbitrary LibreSprite JavaScript for development/debugging. It is intentionally unsafe and is never enabled by default.

## Image feedback

The bridge can return visible cel images. RGB cels use LibreSprite `Image.getPNGData()`. Indexed/grayscale cels are resolved to RGBA before leaving the bridge so palette indices are not mistaken for packed RGBA pixels. Python composites visible cels into a canvas PNG and returns actual MCP image content. Contact sheets are generated with nearest-neighbour scaling only.

## Unsupported capabilities

The server reports features as unsupported instead of simulating success when the current LibreSprite scripting surface lacks a verified non-interactive route. This currently includes frame-duration editing, frame tags, and parameterized spritesheet export.
