# playable-kit — integration guide for AI agents

Unpack, re-skin, repack, and convert **PixiJS + webpack playable ads** whose assets are inlined as
base64 data URIs. The source builds target **AppLovin (MRAID)**. The kit can emit **AppLovin** (`.html`) and
**Mintegral** (`.zip` containing a single `index.html`).

Use the CLI with `--json` (any language), or import the Python API. Both behave the same way.

> **Use only on playables you own or are licensed to modify.** Do not use this kit to rebrand, re-skin or
> re-target another company's ad creative. If the build contains another app's store links, branding, or
> analytics IDs, stop and ask the user before continuing.

## Install
```bash
pip install ./playable-kit              # or: pip install playable_kit-0.1.0-py3-none-any.whl
playable-kit --version                  # also: python -m playable_kit
```
Requires Python ≥ 3.9 and Pillow. The `smoke` command also needs Node ≥ 22 and Chrome/Chromium.

## Standard workflow
```bash
playable-kit --json inspect  build.html                        # 1. is it supported?
playable-kit --json unpack   build.html -o ws/                 # 2. extract assets into a workspace
#   ...replace files under ws/assets/ (same file names), edit ws/playable.json...
playable-kit --json pack     ws/ [-n mintegral] [--quantize]   # 3. rebuild -> ws/dist/
playable-kit --json validate ws/dist/<name>_mintegral.zip      # 4. static checks
playable-kit --json smoke    ws/dist/<name>_mintegral.zip     # 5. runtime check in headless Chrome
```
To convert without changing assets: `playable-kit --json convert build.html --to mintegral -o out.zip`.

## Output contract
- With `--json`, stdout contains **exactly one JSON object**. Human-readable text and warnings are not printed.
- Every object has `ok` (bool) and `command`. On failure it also has `error: {code, message, details?}`.
- Exit codes: `0` ok · `1` operation failed · `2` bad CLI usage · `3` `validate`/smoke ran and at least one check failed.

| error.code | meaning | what to do |
|---|---|---|
| `unsupported_build` | Not a PixiJS/webpack inline-asset build (e.g. Luna/Unity, Cocos, assets loaded from URLs) | Stop. Do not try to patch it. Report to the user. |
| `workspace_error` | Workspace missing/corrupt, or `unpack` would overwrite an existing one | Pass `--force` **only** if losing the edits in `assets/` is acceptable |
| `asset_error` | An asset file was deleted or is not a valid image | Restore the file (same name) |
| `conversion_error` | Unknown network, or the bootstrap script is not the expected MRAID one | Stop and report |
| `smoke_unavailable` | Node >= 22 or Chrome missing / failed to start | Install or set `CHROME_PATH`; fall back to `validate` and tell the user runtime was not verified |
| `internal_error` | Bug | Report with the message |

### `inspect FILE` (.html or .zip)
```json
{"ok":true,"command":"inspect","file":"...","bytes":2398947,"ad_network":"applovin",
 "store_links":{"ios":"https://apps.apple.com/...","android":"https://play.google.com/..."},
 "analytics_enabled":false,"analytics":"gated-off","external_urls":[],"supported":true,
 "asset_modules":{"images":23,"sounds":11,"spritesheets":8},
 "spritesheets":{"items":118,"ui":7}}
```
`supported:false` comes with `unsupported_reason:{code,message}`. `analytics` is one of `none`, `stripped`
(the `send()` body is empty), `gated-off` (analytics code is present but `applicationSettings.analytics` is
false, so nothing is sent) or `active` (it can reach the network); `analytics_enabled` is the boolean form.
Surface `active` to the user before they ship the build.

### `unpack FILE -o WORKSPACE [--force]`
Returns `{workspace, template, config, assets, spritesheets, frames, files:[...]}`. `files` lists every editable
path relative to `WORKSPACE/assets/`.

### `pack WORKSPACE [-o OUT_DIR] [-n NETWORK ...] [--quantize]`
```json
{"ok":true,"command":"pack","workspace":"...",
 "outputs":[{"network":"mintegral","path":".../ws/dist/mygame_mintegral.zip","bytes":1379067,"mb":1.315,"over_size_limit":false}],
 "changed_assets":["sprites/items/apple_0.png"],"changed_spritesheets":["items"],
 "warnings":[{"code":"size_changed","file":"images/back.jpg","original":[1640,1640],"new":[1024,1024]}]}
```
Warning codes: `size_changed` (the object will render bigger or smaller), `unknown_file_ignored` (new file name the game
never references), `over_size_limit` (> 5 MB; retry with `--quantize` or smaller assets).

### `convert FILE --to NETWORK [-o OUTPUT]`
Returns a single output object like the items in `pack.outputs`. Converting a build that is already Mintegral
returns it unchanged.

### `validate FILE [-n NETWORK]`
`{ok, network, checks:[{id,status:"pass"|"warn"|"fail",message}], external_urls, store_links}`. `ok` is false
if any check has status `fail`.
Mintegral checks: `size_limit, no_external_urls(warn), analytics_disabled(warn), assets_inline, zip_single_index_html(warn),
calls_gameReady, calls_install, calls_gameEnd, bridge_matches_engine, defines_gameStart, defines_gameClose, no_mraid_open`.
AppLovin: `mraid_bootstrap` plus the common checks.

### `smoke FILE [-n NETWORK] [--seconds 6] [--screenshot out.png]`
Loads the build in headless Chrome with a mocked SDK, then triggers `window.application.clickInstall()`.
`{ok, network, file, screenshot, checks:[{id,pass,detail}]}`. Needs Node >= 22 and Chrome (`CHROME_PATH` env to override); if missing -> error `smoke_unavailable`. Checks: `no_js_exceptions, canvas_rendered, no_external_requests`,
plus Mintegral `gameReady_called, cta_calls_install, gameEnd_called` or AppLovin `cta_calls_mraid_open`.
Always run it before calling a build "working". `validate` is static and cannot detect a black screen.

## Workspace layout (after `unpack`)
```
ws/
  playable.json            EDIT: {"name": "mygame", "store": {"ios": "...", "android": "..."}}
  assets/images/*.png|jpg  EDIT: standalone images (replace in place)
  assets/sounds/*.mp3      EDIT: sounds
  assets/sprites/<sheet>/<frame>.png
                           EDIT: every spritesheet frame at its full, un-rotated, untrimmed size
  template.html            DO NOT EDIT: game code with {{PK:...}} placeholders
  .playable-kit/           DO NOT EDIT: manifest (hashes, frame metadata) and original atlases
  dist/                    pack output
```

## Rules and limits
1. **Replace, never rename or add.** The game references assets by name. New names are ignored and produce a warning.
2. **Keep pixel dimensions** unless you intend to change the on-screen size. Keep transparency (RGBA) for sprites.
3. A spritesheet is re-packed only if one of its frames changed. Otherwise the original atlas bytes are reused, so
   an unmodified workspace round-trips **byte-identical** (store links aside). Re-packed atlases are not rotated,
   are alpha-trimmed, and keep extra frame fields such as `pivot`.
4. `--quantize` makes re-packed atlases 256-colour. They are smaller, with slight banding on gradients.
5. Gameplay (orders, timings, layout, when the CTA appears) lives in the code, not in assets. This kit does not change it.
   On-screen text is usually baked into images.
6. Mintegral bridge: `gameReady()` fires after `application.init()` resolves. `gameStart()` unmutes and
   `gameClose()` mutes. The CTA calls `gameEnd()` (once) and then `install()`. Mintegral ignores the store links
   inside the file and uses the campaign's link.
7. **Supported builds** have `importAll(i(<id>), j.ASSETS_TYPES.<kind>)`, webpack `require.context` maps
   (`{"./name.ext":<moduleId>}`), `<id>(a){"use strict";a.exports="data:...;base64,..."}` modules, and a
   `window.onload=function(){...mraid...window.application...}` bootstrap. Run `inspect` first. Anything else
   (Luna/Unity, Cocos, Phaser single-file) returns `unsupported_build`.
8. `unpack` refuses to overwrite a workspace unless `--force` is passed. Never pass `--force` on a workspace that
   has edits without the user's approval.

## Python API
```python
import playable_kit as pk
pk.inspect(path) -> dict
pk.unpack(html_path, workspace, force=False) -> dict
pk.pack(workspace, out_dir=None, network_list=("applovin", "mintegral"), quantize=False) -> dict
pk.build_html(workspace, quantize=False) -> (html_str, report)   # AppLovin HTML in memory
pk.convert(html_path, network, out_path=None) -> dict
pk.validate(path, network=None) -> dict
pk.SUPPORTED_NETWORKS  # {"applovin": fn, "mintegral": fn}
# errors: pk.PlayableKitError (.code, .message, .details, .to_dict()) and its subclasses
```

## Adding another network
Add an adapter `to_<net>(html) -> html` in `playable_kit/networks.py`. Register it in `ADAPTERS`,
`OUTPUT_FORMAT`, and `SIZE_LIMIT_MB`. Add its checks in `core.validate` and its mock in `playable_kit/smoke_test.mjs`.
Most MRAID networks (Unity Ads, ironSource, Vungle) can use the AppLovin output as it is.

## Tests
```bash
pip install -e .[test] && pytest -q
PLAYABLE_KIT_SAMPLE=path/to/real_build.html pytest -q   # adds a byte-identical round-trip test on a real build
```
