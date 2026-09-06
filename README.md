# LibreSprite-MCP (Astra Safe Fork)

A safety-hardened fork of **Snehil-Shah/LibreSprite-MCP** focused on letting a multimodal agent edit and inspect real LibreSprite animation documents without Computer Use.

The default is **SAFE mode**. SAFE mode exposes only typed, whitelisted operations. Arbitrary JavaScript execution is not registered as an MCP tool and the LibreSprite bridge rejects it. **DEV mode** exists only for debugging and must be enabled explicitly.

## What this fork is for

The MCP provides mechanical editing primitives only: frames, layers, cels, pixels, regions, palette, previews, save/export and inspection. It deliberately contains **no walk-cycle generator, pose library, animation tutorial, artistic heuristic or auto-animation system**. The agent makes the artistic decisions.

## Security model

- Relay binds to `127.0.0.1` only.
- Bridge pairing is trust-on-first-use on loopback; the first bridge receives a cryptographically random session token.
- After pairing, `/next` and `/result` require the token and request IDs.
- SAFE mode has an explicit operation whitelist.
- No shell, PowerShell, subprocess, arbitrary Python, or generic HTTP tool is exposed.
- File tools can be constrained with `LIBRESPRITE_MCP_ALLOWED_ROOT`.
- Requests have timeouts and payload limits; stale/unknown request IDs are rejected.
- `run_script` is registered **only** in DEV mode and the bridge checks DEV mode again before evaluating it.

The one-time pairing endpoint is intentionally a local TOFU mechanism: a malicious local process that races LibreSprite to pair first could claim the session. SAFE mode still limits the paired client to the whitelisted dispatcher. See `docs/SECURITY.md`.

## Windows install

1. Install LibreSprite from its normal official distribution.
2. Install `uv` with WinGet:

```powershell
winget install --id=astral-sh.uv -e
```

3. Clone this fork and switch to the hardened branch:

```powershell
git clone https://github.com/indira0003/LibreSprite-MCP.git
cd LibreSprite-MCP
git switch astra-safe-animation-mcp
```

4. Optional but recommended: restrict file operations to your sprite workspace:

```powershell
$env:LIBRESPRITE_MCP_ALLOWED_ROOT="C:\Users\YOUR_NAME\Documents\Sprites"
```

5. Run the MCP locally for a quick check:

```powershell
uv run libresprite-mcp
```

The normal transport is stdio, so in day-to-day use your MCP client launches it.

## Install the LibreSprite bridge

Copy:

```text
remote/mcp.js
```

into LibreSprite's scripts folder. In LibreSprite, rescan/open scripts and run `mcp.js`. A small dialog appears. Start your MCP client first, then click **Connect**. The bridge pairs to `127.0.0.1:64823`.

If LibreSprite reports that `storage.fetch` is unavailable, that LibreSprite build cannot use this relay. The bridge does not invent an unsafe fallback.

## MCP client configuration

For a checkout of this fork:

```json
{
  "mcpServers": {
    "libresprite": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\path\\to\\LibreSprite-MCP",
        "libresprite-mcp"
      ],
      "env": {
        "LIBRESPRITE_MCP_MODE": "safe",
        "LIBRESPRITE_MCP_ALLOWED_ROOT": "C:\\Users\\YOUR_NAME\\Documents\\Sprites"
      }
    }
  }
}
```

SAFE mode is the default even if `LIBRESPRITE_MCP_MODE` is omitted.

## Confirm SAFE mode

Call:

```text
health_check
```

Expected fields include:

```json
{
  "mode": "safe",
  "protocol_version": 1
}
```

and `get_capabilities` should report:

```json
"arbitrary_script": false
```

`run_script` must not appear in the MCP tool list.

## DEV mode

Only for development/debugging:

```powershell
uv run libresprite-mcp --mode dev
```

DEV mode registers `run_script` and allows arbitrary LibreSprite JavaScript. Do not use DEV mode for the Astra evaluation.

## Important current LibreSprite limitations

The upstream scripting API currently exposes `FrameProperties` as a UI-only command and does not expose a verified non-interactive frame-duration setter/getter. This fork therefore reports frame-duration tools as unsupported rather than pretending they worked.

Likewise, frame tags and fully parameterized non-interactive spritesheet export are reported unsupported until an upstream scripting API is verified.

PNG/GIF export uses LibreSprite's normal `saveAs(..., asCopy=true)` behavior. Live integration testing on the exact LibreSprite build is still required before treating a build-specific export path as verified.

## Testing

```powershell
uv sync --extra dev
uv run pytest
uv run pip-audit
```

The repository includes unit tests that do not require LibreSprite and a CI workflow. Live LibreSprite tests are intentionally separate because a GitHub runner has no interactive LibreSprite session.

## Tool reference

See [docs/TOOLS.md](docs/TOOLS.md).

## License

GPL-2.0-only, preserving the original project's license.
