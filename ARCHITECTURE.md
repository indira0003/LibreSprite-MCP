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

The relay binds to `127.0.0.1`. LibreSprite performs loopback pairing and receives a cryptographically random session token. Subsequent `/next`, `/result` and `/disconnect` traffic sends that token in the `X-LibreSprite-Token` header. The token is never placed in URLs/query strings.

An authenticated session has a 30-second inactivity lease. Expiration clears bridge metadata, cancels old queued/in-flight calls, and invalidates the token. A new script can then pair without restarting Python. A per-script nonce makes retries of a lost pairing response idempotent; it is not persisted or logged. A different script cannot replace an active lease. Explicit disconnect releases it immediately when delivery succeeds.

`/next` responds immediately, including when idle. LibreSprite waits at least 500 ms between idle polls and backs off from 500 ms to 5 seconds after errors. `app.yield(event, cycles)` drives scheduling, while `Date.now()` measures milliseconds. Each native fetch has a unique storage key and its callback is registered before fetching. Late callbacks cannot overwrite current requests; an 8-second watchdog recovers stored replies when their events are lost. Three consecutive missing native completions stop polling to bound outstanding native requests, since `storage.fetch` has no cancellation API.

The relay delivers one operation ID at a time. A lost `/next` reply redelivers the same ID; the bridge retains the last result instead of executing it again. Failed result posts retry that result, and the relay acknowledges duplicate results idempotently. Expired queued operations are removed before they can execute. An already delivered operation can have an unknown outcome on timeout: the caller is told to inspect the sprite, and no automatic edit retry crosses a session change. This is not a claim of exactly-once execution across editor/process crashes.

The dialog and widgets are retained and updated in place. Closing the dialog stops the script's poll loop, with best-effort disconnect and lease expiry as fallback. LibreSprite uses a shared scripting engine: running another unrelated script may replace its context, so keep `mcp.js` as the active script during an agent editing session.

This is a local TOFU design, not a claim that a compromised local machine can be made trustworthy. SAFE mode still restricts the paired bridge to its operation whitelist.

## DEV mode

`--mode dev` explicitly registers `run_script` and permits arbitrary LibreSprite JavaScript for development/debugging. It is intentionally unsafe and is never enabled by default.

## Image feedback

The bridge can return visible cel images. RGB cels use LibreSprite `Image.getPNGData()`. Indexed/grayscale cels are resolved to RGBA before leaving the bridge so palette indices are not mistaken for packed RGBA pixels. Python composites visible cels into a canvas PNG and returns actual MCP image content. Contact sheets are generated with nearest-neighbour scaling only.

## Unsupported capabilities

The server reports features as unsupported instead of simulating success when the current LibreSprite scripting surface lacks a verified non-interactive route. This currently includes frame-duration editing, frame tags, and parameterized spritesheet export.
