"""Public operations: inspect, unpack, pack, convert, validate.

All functions return plain JSON-serialisable dicts and raise PlayableKitError subclasses.
"""
import hashlib
import io
import json
import os
import re
import shutil
import zipfile

from PIL import Image

from . import networks, spritesheet
from .errors import AssetError, ConversionError, UnsupportedBuildError, WorkspaceError
from .webpack_assets import data_uri, find_asset_modules, strip_data_uris

WORKSPACE_VERSION = 1
PH_RE = re.compile(r"\{\{PK:([a-z-]+):([^}]+)\}\}")
MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".avif": "image/avif", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
               ".m4a": "audio/mp4", ".json": "application/json", ".atlas": "text/plain", ".txt": "text/plain"}
URL_ALLOWLIST = ("http://www.w3.org/",
                 "https://github.com/mitsuhiko/webgl-meincraft")  # comment string inside PixiJS itself
_BAD_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')


def _sha1(b):
    return hashlib.sha1(b).hexdigest()


def _read_text(path):
    try:
        return open(path, encoding="utf-8").read()
    except FileNotFoundError:
        raise WorkspaceError(f"File not found: {path}")


def _read_html_any(path):
    """Read an .html file, or the single index.html inside a .zip."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            htmls = [n for n in z.namelist() if n.lower().endswith(".html")]
            if not htmls:
                raise UnsupportedBuildError("Zip contains no .html file.", files=z.namelist())
            name = "index.html" if "index.html" in htmls else htmls[0]
            return z.read(name).decode("utf-8"), z.namelist()
    return _read_text(path), None


def _pair_sheets(modules):
    """Map spritesheet json module -> atlas image module."""
    images = {m.name: m for m in modules.values() if m.kind == "images"}
    pairs = {}
    for m in modules.values():
        if m.kind != "spritesheets":
            continue
        key = m.name[:-5] if m.name.endswith(".json") else m.name
        meta_image = spritesheet.load_json(m.data).get("meta", {}).get("image", "")
        base_dir = os.path.dirname(key)
        candidates = [meta_image, os.path.join(base_dir, meta_image),
                      f"spritesheet_{key}.png", os.path.join(base_dir, "spritesheet_" + os.path.basename(key) + ".png"),
                      f"{key}.png"]
        img = next((images[c.replace("\\", "/")] for c in candidates if c and c.replace("\\", "/") in images), None)
        if img is None:
            raise UnsupportedBuildError(f"Cannot find the atlas image for spritesheet '{m.name}'.",
                                        tried=[c for c in candidates if c])
        pairs[key] = (m, img)
    return pairs


def _send_body(html):
    """Body of the analytics send() method, located by brace matching."""
    m = re.search(r'playableId\s*=\s*"[^"]*"[\s\S]{0,400}?send\(\w*\)\{', html)
    if not m:
        return None
    start = m.end()
    depth = 1
    for i in range(start, len(html)):
        c = html[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return html[start:i]
    return None


def _analytics_state(html):
    """none | stripped | gated-off (present but disabled by a flag) | active"""
    body = _send_body(html)
    if body is None:
        return "none"
    body = body.strip()
    if not body or re.fullmatch(r"(/\*.*?\*/|//[^\n]*)", body, re.S):
        return "stripped"
    if not re.search(r"fetch\(|sendBeacon|XMLHttpRequest|WebSocket", body):
        return "stripped"
    gated = re.search(r"if\s*\(\s*window\.applicationSettings\.analytics\s*\)", body)
    flag_off = re.search(r"window\.applicationSettings\s*=\s*\{[^}]*analytics\s*:\s*(!1|false)", html)
    return "gated-off" if gated and flag_off else "active"


def _analytics_enabled(html):
    state = _analytics_state(html)
    return None if state == "none" else state == "active"


def _external_urls(html):
    text = strip_data_uris(html)
    urls = sorted(set(re.findall(r'https?://[^\s"\'`<>)\\]+', text)))
    stores = networks.get_store_links(html) or {}
    store_vals = set(stores.values())
    return [u for u in urls if u not in store_vals and not u.startswith(URL_ALLOWLIST)]


# --------------------------------------------------------------------------- inspect
def inspect(html_path):
    html, zip_files = _read_html_any(html_path)
    result = {"file": os.path.abspath(html_path), "bytes": os.path.getsize(html_path),
              "ad_network": networks.detect_network(html), "store_links": networks.get_store_links(html),
              "analytics_enabled": _analytics_enabled(html), "analytics": _analytics_state(html),
              "external_urls": _external_urls(html)}
    if zip_files is not None:
        result["zip_files"] = zip_files
    try:
        modules = find_asset_modules(html)
        pairs = _pair_sheets(modules)
        kinds = {}
        for m in modules.values():
            kinds[m.kind] = kinds.get(m.kind, 0) + 1
        result.update(supported=True, asset_modules=kinds,
                      spritesheets={k: len(spritesheet.load_json(j.data)["frames"]) for k, (j, _) in pairs.items()})
    except UnsupportedBuildError as e:
        result.update(supported=False, unsupported_reason=e.to_dict())
    return result


# --------------------------------------------------------------------------- unpack
def unpack(html_path, workspace, force=False):
    html = _read_text(html_path)
    modules = find_asset_modules(html)
    pairs = _pair_sheets(modules)
    sheet_mids = {m.module_id for pair in pairs.values() for m in pair}

    ws = os.path.abspath(workspace)
    state_dir = os.path.join(ws, ".playable-kit")
    if os.path.exists(os.path.join(ws, "template.html")) or os.path.isdir(os.path.join(ws, "assets")):
        if not force:
            raise WorkspaceError("Workspace already contains an unpacked playable; pass force=True (--force) "
                                 "to overwrite assets/ and template.html.", workspace=ws)
        shutil.rmtree(os.path.join(ws, "assets"), ignore_errors=True)
        shutil.rmtree(state_dir, ignore_errors=True)
    os.makedirs(os.path.join(state_dir, "orig"), exist_ok=True)

    def write(rel, data):
        p = os.path.join(ws, "assets", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    manifest = {"version": WORKSPACE_VERSION, "source": os.path.basename(html_path),
                "source_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(), "assets": {}, "sheets": {}}
    placeholders = {}

    for m in modules.values():
        if m.module_id in sheet_mids:
            continue
        rel = f"{m.kind}/{m.name}"
        write(rel, m.data)
        info = {"mime": m.mime, "sha1": _sha1(m.data)}
        if m.mime.startswith("image/"):
            try:
                info["size"] = list(Image.open(io.BytesIO(m.data)).size)
            except Exception:
                pass
        manifest["assets"][rel] = info
        placeholders[m.span] = "{{PK:asset:%s}}" % rel

    for key, (jm, im) in sorted(pairs.items()):
        meta, frames = spritesheet.explode(jm.data, im.data)
        entries, used = {}, set()
        for name, img, extra in frames:
            short = name[len(key) + 1:] if name.startswith(key + "/") else name
            rel = f"sprites/{key}/{_BAD_CHARS.sub('_', short)}.png"
            while rel.lower() in used:
                rel = rel[:-4] + "_dup.png"
            used.add(rel.lower())
            buf = io.BytesIO()
            img.save(buf, "PNG")
            write(rel, buf.getvalue())
            entries[name] = {"file": rel, "sha1": _sha1(buf.getvalue()), "size": list(img.size), "extra": extra}
        safe = key.replace("/", "__")
        with open(os.path.join(state_dir, "orig", safe + ".json"), "wb") as f:
            f.write(jm.data)
        with open(os.path.join(state_dir, "orig", safe + ".png"), "wb") as f:
            f.write(im.data)
        manifest["sheets"][key] = {"orig": safe, "meta": meta, "frames": entries}
        placeholders[jm.span] = "{{PK:sheet-json:%s}}" % key
        placeholders[im.span] = "{{PK:sheet-png:%s}}" % key

    parts, last = [], 0
    for (a, b), ph in sorted(placeholders.items()):
        parts += [html[last:a], ph]
        last = b
    parts.append(html[last:])
    with open(os.path.join(ws, "template.html"), "w", encoding="utf-8") as f:
        f.write("".join(parts))
    with open(os.path.join(state_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)

    cfg_path = os.path.join(ws, "playable.json")
    if not os.path.exists(cfg_path):
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(html_path))[0]) or "playable"
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"name": name, "store": networks.get_store_links(html) or {}}, f, indent=2, ensure_ascii=False)

    return {"workspace": ws, "template": os.path.join(ws, "template.html"), "config": cfg_path,
            "assets": len(manifest["assets"]), "spritesheets": len(manifest["sheets"]),
            "frames": sum(len(s["frames"]) for s in manifest["sheets"].values()),
            "files": sorted(manifest["assets"]) + sorted(f["file"] for s in manifest["sheets"].values()
                                                          for f in s["frames"].values())}


# --------------------------------------------------------------------------- pack
def _load_workspace(ws):
    mpath = os.path.join(ws, ".playable-kit", "manifest.json")
    if not os.path.exists(mpath):
        raise WorkspaceError(f"Not a playable-kit workspace (missing {mpath}). Run unpack first.")
    manifest = json.load(open(mpath, encoding="utf-8"))
    config = json.load(open(os.path.join(ws, "playable.json"), encoding="utf-8"))
    return manifest, config, _read_text(os.path.join(ws, "template.html"))


def build_html(workspace, quantize=False):
    """Fill template.html from assets/. Returns (html, report)."""
    ws = os.path.abspath(workspace)
    manifest, config, tpl = _load_workspace(ws)
    warnings, changed_assets, changed_sheets, values = [], [], [], {}

    def read(rel):
        try:
            return open(os.path.join(ws, "assets", rel), "rb").read()
        except FileNotFoundError:
            raise AssetError(f"Missing asset file: assets/{rel}", file=rel)

    for rel, info in manifest["assets"].items():
        data = read(rel)
        if _sha1(data) != info["sha1"]:
            changed_assets.append(rel)
            if info.get("size"):
                try:
                    size = list(Image.open(io.BytesIO(data)).size)
                except Exception:
                    raise AssetError(f"assets/{rel} is not a readable image", file=rel)
                if size != info["size"]:
                    warnings.append({"code": "size_changed", "file": rel, "original": info["size"], "new": size})
        values[("asset", rel)] = data_uri(data, MIME_BY_EXT.get(os.path.splitext(rel)[1].lower(), info["mime"]))

    for key, s in manifest["sheets"].items():
        frames, dirty = [], False
        for name, f in s["frames"].items():
            data = read(f["file"])
            try:
                img = Image.open(io.BytesIO(data)).convert("RGBA")
            except Exception:
                raise AssetError(f"assets/{f['file']} is not a readable image", file=f["file"])
            if _sha1(data) != f["sha1"]:
                dirty = True
                changed_assets.append(f["file"])
                if list(img.size) != f["size"]:
                    warnings.append({"code": "size_changed", "file": f["file"], "original": f["size"], "new": list(img.size)})
            frames.append((name, img, f["extra"]))
        known = {f["file"] for f in s["frames"].values()}
        sheet_dir = os.path.join(ws, "assets", "sprites", key)
        for root, _, files in os.walk(sheet_dir):
            for fn in files:
                rel = os.path.relpath(os.path.join(root, fn), os.path.join(ws, "assets")).replace("\\", "/")
                if rel not in known:
                    warnings.append({"code": "unknown_file_ignored", "file": rel,
                                     "message": "Not referenced by the game; only existing file names can be replaced."})
        orig = os.path.join(ws, ".playable-kit", "orig", s["orig"])
        if dirty:
            changed_sheets.append(key)
            sj, sp = spritesheet.repack(frames, s["meta"], quantize=quantize)
        else:
            sj, sp = open(orig + ".json", "rb").read(), open(orig + ".png", "rb").read()
        values[("sheet-json", key)] = data_uri(sj, "application/json")
        values[("sheet-png", key)] = data_uri(sp, "image/png")

    def sub(m):
        k = (m.group(1), m.group(2))
        if k not in values:
            raise WorkspaceError(f"template.html references unknown placeholder {m.group(0)}")
        return values[k]

    html = PH_RE.sub(sub, tpl)
    store = config.get("store") or {}
    if store.get("ios") or store.get("android"):
        html = networks.set_store_links(html, store.get("ios"), store.get("android"))
    return html, {"name": config.get("name", "playable"), "changed_assets": changed_assets,
                  "changed_spritesheets": changed_sheets, "warnings": warnings}


def _write_output(html, network, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    converted = networks.ADAPTERS[network](html)
    if networks.OUTPUT_FORMAT[network] == "zip":
        path = os.path.join(out_dir, f"{name}_{network}.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("index.html", converted)
    else:
        path = os.path.join(out_dir, f"{name}_{network}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(converted)
    mb = os.path.getsize(path) / 1024 / 1024
    return {"network": network, "path": path, "bytes": os.path.getsize(path), "mb": round(mb, 3),
            "over_size_limit": mb > networks.SIZE_LIMIT_MB[network]}


def pack(workspace, out_dir=None, network_list=("applovin", "mintegral"), quantize=False):
    for n in network_list:
        if n not in networks.ADAPTERS:
            raise ConversionError(f"Unknown network '{n}'", supported=sorted(networks.ADAPTERS))
    ws = os.path.abspath(workspace)
    html, report = build_html(ws, quantize=quantize)
    out_dir = os.path.abspath(out_dir or os.path.join(ws, "dist"))
    outputs = [_write_output(html, n, out_dir, report["name"]) for n in network_list]
    for o in outputs:
        if o["over_size_limit"]:
            report["warnings"].append({"code": "over_size_limit", "file": o["path"],
                                       "message": f"{o['mb']} MB > {networks.SIZE_LIMIT_MB[o['network']]} MB; "
                                                  "retry with quantize=True or smaller assets."})
    return {"workspace": ws, "outputs": outputs, **{k: v for k, v in report.items() if k != "name"}}


# --------------------------------------------------------------------------- convert
def convert(html_path, network, out_path=None):
    if network not in networks.ADAPTERS:
        raise ConversionError(f"Unknown network '{network}'", supported=sorted(networks.ADAPTERS))
    html = _read_text(html_path)
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(html_path))[0])
    out_dir = os.path.dirname(os.path.abspath(out_path)) if out_path else os.path.dirname(os.path.abspath(html_path))
    result = _write_output(html, network, out_dir, name)
    if out_path and os.path.abspath(out_path) != result["path"]:
        os.replace(result["path"], out_path)
        result["path"] = os.path.abspath(out_path)
    return result


# --------------------------------------------------------------------------- validate
def validate(path, network=None):
    html, zip_files = _read_html_any(path)
    network = network or networks.detect_network(html)
    checks = []

    def check(cid, ok, message, level="fail"):
        checks.append({"id": cid, "status": "pass" if ok else level, "message": message})

    size_mb = os.path.getsize(path) / 1024 / 1024
    limit = networks.SIZE_LIMIT_MB.get(network, 5)
    check("size_limit", size_mb <= limit, f"{size_mb:.2f} MB (limit {limit} MB)")

    external = _external_urls(html)
    check("no_external_urls", not external,
          "No URLs besides store links" if not external else f"{len(external)} external URL(s) found; review them",
          level="warn")
    analytics = _analytics_state(html)
    check("analytics_disabled", analytics != "active",
          {"active": "Analytics send() can reach the network",
           "gated-off": "Analytics present but disabled by applicationSettings.analytics=false",
           "stripped": "Analytics send() is a no-op",
           "none": "No analytics module detected"}[analytics], level="warn")
    try:
        find_asset_modules(html)
        check("assets_inline", True, "All asset modules are inlined")
    except UnsupportedBuildError as e:
        # unknown bundler: the build can still ship, it just cannot be re-skinned by this kit
        check("assets_inline", False, f"Asset editing unavailable: {e.message}", level="warn")

    if network == "mintegral":
        if zip_files is not None:
            check("zip_single_index_html", zip_files == ["index.html"], f"zip entries: {zip_files}", level="warn")
        else:
            check("zip_packaging", False, "Mintegral expects a .zip containing index.html", level="warn")
        called = lambda fn: re.search(r"window\.%s\s*\(\s*\)" % fn, html) is not None
        check("calls_gameReady", called("gameReady"), "window.gameReady() is called")
        check("calls_install", called("install"), "CTA calls window.install()")
        check("calls_gameEnd", called("gameEnd"), "window.gameEnd() is called")
        luna_glue = "luna:" in html and "Bridge.define" not in html
        check("bridge_matches_engine", not luna_glue,
              "Luna/Unity event glue on a non-Luna build: SDK callbacks will never fire" if luna_glue
              else "SDK bridge matches the game engine")
        check("defines_gameStart", re.search(r"window\.gameStart\s*=", html) is not None, "window.gameStart is defined")
        check("defines_gameClose", re.search(r"window\.gameClose\s*=", html) is not None, "window.gameClose is defined")
        shimmed = "mtg:start" in html
        check("no_mraid_open", shimmed or ("mraid.open" not in html and "window.open(" not in html),
              "MRAID calls are shimmed to the Mintegral SDK" if shimmed
              else "No mraid.open / window.open in the bootstrap")
    elif network in ("applovin", "mraid"):
        check("mraid_bootstrap", bool(networks.BOOTSTRAP_RE.search(html)) and "mraid.open" in html,
              "MRAID bootstrap with mraid.open CTA")
    else:
        check("known_network", False, f"Unknown/undetected ad network: {network!r}")

    return {"file": os.path.abspath(path), "network": network,
            "ok": all(c["status"] != "fail" for c in checks), "checks": checks,
            "external_urls": external, "store_links": networks.get_store_links(html)}
