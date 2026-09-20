import io
import json
import os
import zipfile

import pytest
from PIL import Image, ImageChops

import playable_kit as pk
from playable_kit import cli, spritesheet
from playable_kit.webpack_assets import find_asset_modules

from .conftest import ANDROID, IOS


def same(a, b):
    return a.size == b.size and ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).getbbox() is None


def sheet_frames_from_html(html, key="food"):
    mods = {m.name: m for m in find_asset_modules(html).values()}
    _, frames = spritesheet.explode(mods[f"{key}.json"].data, mods[f"spritesheet_{key}.png"].data)
    return {n: img for n, img, _ in frames}


def test_inspect(sample):
    path, _ = sample
    r = pk.inspect(str(path))
    assert r["supported"] and r["ad_network"] == "applovin"
    assert r["store_links"] == {"ios": IOS, "android": ANDROID}
    assert r["spritesheets"] == {"food": 3}
    assert r["analytics_enabled"] is False


def test_unpack_restores_frames_exactly(sample, tmp_path):
    path, frames = sample
    r = pk.unpack(str(path), tmp_path / "ws")
    assert (r["assets"], r["spritesheets"], r["frames"]) == (2, 1, 3)
    for name, img in frames.items():
        out = Image.open(tmp_path / "ws" / "assets" / "sprites" / "food" / (name.split("/")[1] + ".png"))
        assert same(out, img), name


def test_roundtrip_is_byte_identical(sample, tmp_path):
    path, _ = sample
    pk.unpack(str(path), tmp_path / "ws")
    r = pk.pack(tmp_path / "ws", network_list=("applovin",))
    assert open(r["outputs"][0]["path"], "rb").read() == path.read_bytes()
    assert r["changed_spritesheets"] == [] and r["warnings"] == []


def test_replaced_frame_is_repacked(sample, tmp_path):
    path, frames = sample
    ws = tmp_path / "ws"
    pk.unpack(str(path), ws)
    new_a = Image.new("RGBA", (10, 6), (0, 128, 255, 255))
    new_a.save(ws / "assets" / "sprites" / "food" / "a.png")
    r = pk.pack(ws, network_list=("applovin",))
    assert r["changed_spritesheets"] == ["food"] and r["warnings"] == []
    got = sheet_frames_from_html(open(r["outputs"][0]["path"], encoding="utf-8").read())
    assert same(got["food/a"], new_a)
    assert same(got["food/b"], frames["food/b"]) and same(got["food/c"], frames["food/c"])


def test_pivot_and_extra_fields_survive_repack(sample, tmp_path):
    path, _ = sample
    ws = tmp_path / "ws"
    pk.unpack(str(path), ws)
    Image.new("RGBA", (10, 6), (1, 2, 3, 255)).save(ws / "assets" / "sprites" / "food" / "a.png")
    html = open(pk.pack(ws, network_list=("applovin",))["outputs"][0]["path"], encoding="utf-8").read()
    mods = {m.name: m for m in find_asset_modules(html).values()}
    doc = json.loads(mods["food.json"].data)
    assert doc["frames"]["food/c"]["pivot"] == {"x": 0.5, "y": 1}


def test_size_change_and_unknown_file_warnings(sample, tmp_path):
    path, _ = sample
    ws = tmp_path / "ws"
    pk.unpack(str(path), ws)
    Image.new("RGB", (32, 32)).save(ws / "assets" / "images" / "bg.png")
    Image.new("RGBA", (4, 4)).save(ws / "assets" / "sprites" / "food" / "new_item.png")
    codes = {(w["code"], w["file"]) for w in pk.pack(ws, network_list=("applovin",))["warnings"]}
    assert ("size_changed", "images/bg.png") in codes
    assert ("unknown_file_ignored", "sprites/food/new_item.png") in codes


def test_missing_asset_raises(sample, tmp_path):
    path, _ = sample
    ws = tmp_path / "ws"
    pk.unpack(str(path), ws)
    os.remove(ws / "assets" / "sounds" / "tap.mp3")
    with pytest.raises(pk.AssetError):
        pk.pack(ws)


def test_store_links_from_config(sample, tmp_path):
    path, _ = sample
    ws = tmp_path / "ws"
    pk.unpack(str(path), ws)
    cfg = json.loads((ws / "playable.json").read_text(encoding="utf-8"))
    cfg["store"] = {"ios": "https://apps.apple.com/us/app/new/id9", "android": "https://play.google.com/x"}
    (ws / "playable.json").write_text(json.dumps(cfg), encoding="utf-8")
    out = pk.pack(ws, network_list=("applovin",))["outputs"][0]["path"]
    assert pk.inspect(out)["store_links"] == cfg["store"]


def test_unpack_refuses_to_overwrite(sample, tmp_path):
    path, _ = sample
    pk.unpack(str(path), tmp_path / "ws")
    with pytest.raises(pk.WorkspaceError):
        pk.unpack(str(path), tmp_path / "ws")
    pk.unpack(str(path), tmp_path / "ws", force=True)


def test_mintegral_conversion_passes_validation(sample, tmp_path):
    path, _ = sample
    ws = tmp_path / "ws"
    pk.unpack(str(path), ws)
    r = pk.pack(ws)
    mtg = next(o for o in r["outputs"] if o["network"] == "mintegral")["path"]
    with zipfile.ZipFile(mtg) as z:
        assert z.namelist() == ["index.html"]
        html = z.read("index.html").decode()
    assert "mraid.open" not in html and 'adNetwork:"mintegral"' in html and "is_applovin" not in html
    v = pk.validate(mtg)
    assert v["network"] == "mintegral" and v["ok"], v["checks"]
    assert pk.validate(next(o for o in r["outputs"] if o["network"] == "applovin")["path"])["ok"]


def test_convert_equals_pack_and_is_idempotent(sample, tmp_path):
    path, _ = sample
    out = pk.convert(str(path), "mintegral", str(tmp_path / "c.zip"))["path"]
    html = zipfile.ZipFile(out).read("index.html").decode()
    from playable_kit.networks import to_mintegral
    assert to_mintegral(html) == html


def test_validate_flags_luna_glue_on_pixi_build(sample, tmp_path):
    path, _ = sample
    html = path.read_text(encoding="utf-8")
    start = html.index('<script defer="defer">window.onload')
    broken = html[:start] + ('<script>window.addEventListener("luna:started",()=>{window.gameReady&&window.gameReady()}),'
                             'window.gameStart=function(){},window.gameClose=function(){},'
                             'window.addEventListener("luna:ended",()=>{window.gameEnd&&window.gameEnd()});'
                             'Luna.Unity.Playable.InstallFullGame=function(){window.install&&window.install()}</script></body></html>')
    zp = tmp_path / "broken.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("index.html", broken)
    v = pk.validate(str(zp), "mintegral")
    assert not v["ok"]
    assert {c["id"]: c["status"] for c in v["checks"]}["bridge_matches_engine"] == "fail"


def test_unsupported_build(tmp_path):
    p = tmp_path / "x.html"
    p.write_text("<html><body>hello</body></html>")
    assert pk.inspect(str(p))["supported"] is False
    with pytest.raises(pk.UnsupportedBuildError):
        pk.unpack(str(p), tmp_path / "ws")


def test_cli_json_contract(sample, tmp_path, capsys):
    path, _ = sample
    assert cli.main(["--json", "inspect", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["command"] == "inspect"

    assert cli.main(["unpack", str(path), "-o", str(tmp_path / "ws"), "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["--json", "unpack", str(path), "-o", str(tmp_path / "ws")]) == 1
    err = json.loads(capsys.readouterr().out)
    assert err == {"ok": False, "command": "unpack", "error": err["error"]} and err["error"]["code"] == "workspace_error"

    assert cli.main(["--json", "pack", str(tmp_path / "ws"), "-n", "mintegral"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [o["network"] for o in out["outputs"]] == ["mintegral"]

    assert cli.main(["--json", "bogus"]) == 2


@pytest.mark.skipif(not os.environ.get("PLAYABLE_KIT_SAMPLE"), reason="set PLAYABLE_KIT_SAMPLE=<real build.html>")
def test_real_build_roundtrip(tmp_path):
    src = os.environ["PLAYABLE_KIT_SAMPLE"]
    pk.unpack(src, tmp_path / "ws")
    out = pk.pack(tmp_path / "ws", network_list=("applovin",))["outputs"][0]["path"]
    cfg_links = json.loads((tmp_path / "ws" / "playable.json").read_text(encoding="utf-8"))["store"]
    assert pk.inspect(out)["store_links"] == cfg_links
    assert open(out, "rb").read() == open(src, "rb").read()


@pytest.mark.skipif(not os.environ.get("PLAYABLE_KIT_SAMPLE"), reason="set PLAYABLE_KIT_SAMPLE=<real build.html>")
def test_real_build_smoke_mintegral(tmp_path, capsys):
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = pk.convert(os.environ["PLAYABLE_KIT_SAMPLE"], "mintegral", str(tmp_path / "m.zip"))["path"]
    code = cli.main(["--json", "smoke", out])
    r = json.loads(capsys.readouterr().out)
    if not r["ok"] and r.get("error", {}).get("code") == "smoke_unavailable":
        pytest.skip(r["error"]["message"])
    assert code == 0 and r["ok"], r


def test_analytics_state_detection(sample, tmp_path):
    from playable_kit.core import _analytics_state
    path, _ = sample
    base = path.read_text(encoding="utf-8")
    assert _analytics_state(base) == "stripped"
    assert _analytics_state("<html><body>hi</body></html>") == "none"
    sending = 'class A{constructor(){this.playableId="X"}send(t){const{path:e}=this;fetch(e,{method:"POST"})}}'
    assert _analytics_state(sending) == "active"
    gated = ('<script>window.applicationSettings={adNetwork:"applovin",analytics:!1}</script>'
             'class A{constructor(){this.playableId="X"}send(t){if(window.applicationSettings.analytics){'
             'const{path:e}=this;fetch(e,{method:"POST"})}}}')
    assert _analytics_state(gated) == "gated-off"


def test_generic_mraid_build_converts_with_shim(tmp_path):
    """A playable from an unknown engine still converts: we shim MRAID instead of rewriting it."""
    html = ('<html><head><title>Other engine</title></head><body><canvas></canvas>'
            '<script>var link="https://play.google.com/store/apps/details?id=com.other.game";'
            'document.body.onclick=function(){mraid.open(link)};</script></body></html>')
    src = tmp_path / "other.html"
    src.write_text(html, encoding="utf-8")
    r = pk.inspect(str(src))
    assert r["store_links"]["android"].endswith("com.other.game")
    out = pk.convert(str(src), "mintegral", str(tmp_path / "other.zip"))["path"]
    v = pk.validate(out, "mintegral")
    assert v["ok"], v["checks"]


def test_non_mraid_build_is_rejected(tmp_path):
    src = tmp_path / "plain.html"
    src.write_text("<html><body>just a page</body></html>", encoding="utf-8")
    with pytest.raises(pk.ConversionError):
        pk.convert(str(src), "mintegral", str(tmp_path / "plain.zip"))
