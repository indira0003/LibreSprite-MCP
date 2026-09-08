# Security notes

## SAFE vs DEV

SAFE is the default. The Python server validates operation names before enqueueing them. The JavaScript bridge independently dispatches named operations through a `switch` and refuses `run_script` unless the paired relay reported DEV mode.

DEV mode intentionally reintroduces arbitrary JavaScript execution for debugging. It is not appropriate for an autonomous agent evaluation.

## Local relay

The HTTP relay binds only to loopback. It uses:
- loopback pairing with a 30-second inactivity lease and token rotation,
- a 256-bit-class random URL-safe session token,
- request IDs,
- bounded queues,
- finite tool timeouts,
- request body limits,
- stale/unknown result rejection.
- browser Origin and non-loopback Host rejection.

Expired/replaced sessions cancel pending calls and reject old tokens. Lost pairing replies can be retried using the same per-script nonce. This nonce is a local retry identifier, not a claim of cryptographic authentication against other local processes. Duplicate result acknowledgments are idempotent; a timed-out delivered edit can have an unknown outcome and must be inspected before retrying.

Pairing is TOFU. A malicious process already running on the same machine could race the real bridge to pair first. After pairing, SAFE mode still limits the client to the whitelisted dispatcher.

## Files

`LIBRESPRITE_MCP_ALLOWED_ROOT` should be set for normal use. Open/save/export paths are normalized in Python and constrained to that root when configured. Windows UNC/device paths are rejected by the validator.

## Network

There is no generic HTTP/fetch MCP tool. `storage.fetch` is used only by the bridge to communicate with the loopback relay.

## Dependency policy

The fork keeps the MCP Python SDK on the 1.x line (`>=1.23,<2`) because MCP 2.x is a breaking migration. Flask/Werkzeug/Pillow are bounded to compatible maintained release lines. CI runs `pip-audit`.

## Known capability gaps

Frame duration editing is not claimed as working because upstream `FrameProperties` is UI-only and the current scripting API does not expose a verified direct setter. Frame-tag and parameterized spritesheet APIs are also not claimed without a verified non-interactive route.
