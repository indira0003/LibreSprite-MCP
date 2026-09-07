# Transport repair investigation — 2026-09-07

## Evidence and limits

Baseline: `277e3aa` on `astra-safe-animation-mcp`. The configured Windows installation was a ZIP extraction, not a Git checkout. Its Python relay matched the branch, and its `mcp.js` had been moved into LibreSprite's user scripts directory.

The original bridge paired and polled successfully for 20 seconds in the installed LibreSprite 1.1-dev executable, including repeated two-second idle responses. Thus **the historical HTTP 0 cannot honestly be attributed to long polling**. Its original socket/cURL error was not recorded. The old implementation discards that diagnostic information, so the exact initiating failure remains unknown.

The repaired bridge was tested against that same executable with an intentionally stopped relay listener. The native log recorded `HTTP 0`, then `HTTP 401 UNAUTHORIZED` after restart, then successful re-pairing and subsequent frame/health operations. This proves recovery from a real transport failure, not the cause of an earlier unrecorded incident.

## Primary sources consulted

Sources were inspected at LibreSprite commit `77855cf3092b333d19afd9c870e58c01b2f06d01`; the available source checkout for the custom build also has the same cURL request behavior.

- [storage_script.cpp](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/script/api/storage_script.cpp): fetch takes URL/key/domain, then header pairs or `POST`/body. Completion writes body and `<key>_status`, then raises `<key>_fetch`. Storage is keyed by script filename/domain and must be unloaded to avoid accumulation.
- [http.h](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/res/http.h) and [http_request.cpp](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/net/http_request.cpp): failed `curl_easy_perform` becomes status 0. The one-millisecond timeout occurs only in `abort()`, not in normal fetches. A successful request returns its HTTP status.
- [app_script.cpp](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/script/api/app_script.cpp) and [task_manager.h](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/task_manager.h): yield counts task-manager cycles; its timer is nominally 10 ms, with worker scheduling and UI load adding latency.
- [app_scripting.cpp](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/script/app_scripting.cpp) and [dialog.h](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/ui/dialog.h): events are delayed and the script engine is shared. Dialog close events use the dialog ID. Programmatic `close()` does not itself prove a user-close callback occurred.
- [Official scripting reference](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/SCRIPTING.md) and [HTTP example](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/data/scripts/examples/http.js).
- [Original LibreSprite MCP bridge](https://github.com/Snehil-Shah/LibreSprite-MCP/blob/main/remote/mcp.js): comparison of fetch/event/polling conventions; it is not used as proof of reliability or as a source of arbitrary-script execution in SAFE.
- [MCP STDIO specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#stdio): newline JSON-RPC stays on stdout; diagnostics stay on stderr. The relay HTTP protocol is internal, not MCP Streamable HTTP.

## Confirmed defects fixed

1. Pairing used permanent metadata while `connected` used a ten-second heartbeat. An abandoned bridge could be disconnected forever yet always receive ALREADY_PAIRED.
2. Any failed poll/result cleared the token and stopped, preventing authenticated recovery.
3. Timed-out queued operations remained available for later execution.
4. Shared callback slots and storage keys allowed overlapping/late requests to consume the wrong callback.
5. Result delivery was not retry-safe. A lost acknowledgment could break the session after an edit had already happened.
6. Pairing and authentication/queue changes were not one atomic state transition.
7. Rebuilding the dialog was unnecessary and made lifecycle management harder; widgets now update in place.
8. Preview compositing multiplied alpha twice; frame comparison ignored RGB-only changes with equal alpha.

## Validation

Automated tests cover stale pairing, explicit disconnect/reconnect, old tokens after re-pair, idle polling, matching IDs, lost replies, duplicate results, late callbacks, queue expiration, unknown outcomes, concurrent pairing, result validation, SAFE rejection, previews, and actual MCP STDIO initialization.

Two opt-in tests additionally passed against the installed Windows LibreSprite executable: native editor operations plus fault/restart recovery, and complete STDIO-to-editor operations returning MCP PNG image content. Test sprites were 8×8 fixtures. These results do not establish every editor feature or long-duration animation workflow on every LibreSprite build. Frame duration/tag capabilities remain explicitly unsupported; no animation generator was added.
