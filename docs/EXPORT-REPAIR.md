# Native export and nested-event regression (2026-09-08)

The real MCP session drew a 64x64, four-frame cat, read pixels back, compared
frames and saved its editable document. `export_gif` then timed out without
creating a file; subsequent requests could still run while the document's
temporary filename was `.gif`. This is distinct from the unresolved historical
HTTP 0 trigger.

Source confirms that `SpriteScriptObject::saveAs(path, true)` executes
`SaveFileCopyAs`. GIF `onGetFormatOptions` unconditionally opens a foreground
dialog when the context has a UI, even if a filename was supplied. PNG animation
saving also asks to export a sequence. Those native commands enter nested GUI
event loops, which can run scheduled bridge ticks before dispatch returns.

Fixes:

- An `executing` guard prevents ticks from polling another operation inside a
  native command's nested GUI loop. Mock regression explicitly delivers nested
  yield events during save and verifies no extra request or repeated save.
- GUI PNG/GIF handlers return `UPDATE_PYTHON_EXPORT` instead of opening dialogs.
- Python saves a unique native `.aseprite` copy, validates its header, and runs
  the user-configured LibreSprite executable in `--batch` mode with fixed argv,
  a 30-second process timeout and no shell/script. No timing is invented.
- GIF uses native `--save-as`. PNG uses a one-frame `--sheet --frame-range`
  import: upstream `--frame-range` does **not** filter `--save-as` output.
- Only decoded, dimension-checked output replaces the destination atomically.
  Missing/corrupt output, subprocess failure/timeout and missing executable are
  not reported as success. Private intermediates are scoped to the destination
  directory, so allowed-root remains effective for the export operation.
- Editable saves require `.ase` or `.aseprite` to avoid format-option dialogs.
- Hidden/locked layer deletion reports an explicit precondition error before
  calling the native command. The installed build disables hidden deletion;
  newer upstream versions display a confirmation dialog. Make the named layer
  visible first when intentionally deleting it. Live tests cover this sequence.

Opt-in real-executable tests now cover PNG/GIF output pixels, two animation frames
and their 100 ms durations, unchanged editor filename/frame state, full MCP STDIO
exports, relay outage/re-pair and abrupt editor exit/reopen with stale-lease
recovery. These test fixtures are separate from user documents; tests terminate
only their own editor processes. Timing/tag editing remains unsupported; export
preserves existing native animation data, it does not provide a new timing API.

Primary source inspected at upstream commit
`77855cf3092b333d19afd9c870e58c01b2f06d01`:

- [sprite_script.cpp](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/script/api/sprite_script.cpp)
- [gif_format.cpp](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/file/gif_format.cpp)
- [file.cpp](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/file/file.cpp)
- [app.cpp](https://github.com/LibreSprite/LibreSprite/blob/77855cf3092b333d19afd9c870e58c01b2f06d01/src/app/app.cpp)
