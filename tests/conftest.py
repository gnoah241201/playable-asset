"""Builds a small synthetic playable with the same structure as the real builds."""
import base64
import io
import json

import pytest
from PIL import Image

IOS = "https://apps.apple.com/us/app/sample/id1"
ANDROID = "https://play.google.com/store/apps/details?id=com.sample"


def png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def uri(mime, data):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def make_frames():
    """Three frames the game would render: plain, rotated in atlas, trimmed in atlas."""
    a = Image.new("RGBA", (10, 6), (255, 0, 0, 255))
    b = Image.new("RGBA", (8, 4), (0, 0, 255, 255))
    b.putpixel((0, 0), (255, 255, 0, 255))          # marks orientation
    c = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
    c.paste(Image.new("RGBA", (6, 6), (0, 255, 0, 255)), (3, 3))
    return {"food/a": a, "food/b": b, "food/c": c}


def make_sheet(frames):
    atlas = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    atlas.paste(frames["food/a"], (0, 0))
    atlas.paste(frames["food/b"].rotate(-90, expand=True), (12, 0))   # 4x8, rotated clockwise
    atlas.paste(frames["food/c"].crop((3, 3, 9, 9)), (18, 0))          # trimmed 6x6
    doc = {"frames": {
        "food/a": {"frame": {"x": 0, "y": 0, "w": 10, "h": 6}, "rotated": False, "trimmed": False,
                   "spriteSourceSize": {"x": 0, "y": 0, "w": 10, "h": 6}, "sourceSize": {"w": 10, "h": 6}},
        "food/b": {"frame": {"x": 12, "y": 0, "w": 8, "h": 4}, "rotated": True, "trimmed": False,
                   "spriteSourceSize": {"x": 0, "y": 0, "w": 8, "h": 4}, "sourceSize": {"w": 8, "h": 4}},
        "food/c": {"frame": {"x": 18, "y": 0, "w": 6, "h": 6}, "rotated": False, "trimmed": True,
                   "spriteSourceSize": {"x": 3, "y": 3, "w": 6, "h": 6}, "sourceSize": {"w": 12, "h": 12},
                   "pivot": {"x": 0.5, "y": 1}}},
        "meta": {"app": "https://www.codeandweb.com/texturepacker", "image": "spritesheet_food.png",
                 "format": "RGBA8888", "size": {"w": 40, "h": 20}, "scale": "1"}}
    return json.dumps(doc).encode(), png(atlas)


def make_html():
    frames = make_frames()
    sheet_json, sheet_png = make_sheet(frames)
    bg = png(Image.new("RGB", (16, 16), (10, 20, 30)))
    mp3 = b"ID3\x03\x00\x00\x00\x00\x00\x00fake-mp3"
    return (
        '<!doctype html><html><head><script>window.applicationSettings={buttonMute:!0,buttonInstall:!1,'
        'adNetwork:"applovin",versions:void 0,analytics:!1},window.is_applovin=!0</script></head><body>'
        '<script defer="defer">class Yh{constructor(t){this.playableId="PK0001"}send(t){/* analytics disabled */}}'
        '(self.webpackChunk_x=self.webpackChunk_x||[]).push([[574],{'
        '7621(A,I,i){const j=window.application;j.importAll(i(10),j.ASSETS_TYPES.images),'
        'j.importAll(i(11),j.ASSETS_TYPES.sounds),j.importAll(i(12),j.ASSETS_TYPES.spritesheets)},'
        '10(A,I,i){const j={"./bg.png":100,"./spritesheet_food.png":101};function V(A){return i(j[A])}},'
        '11(A,I,i){const j={"./tap.mp3":102};function V(A){return i(j[A])}},'
        '12(A,I,i){const j={"./food.json":103};function V(A){return i(j[A])}},'
        f'100(A){{"use strict";A.exports="{uri("image/png", bg)}"}},'
        f'101(A){{"use strict";A.exports="{uri("image/png", sheet_png)}"}},'
        f'102(A){{"use strict";A.exports="{uri("audio/mpeg", mp3)}"}},'
        f'103(A){{"use strict";A.exports="{uri("application/json", sheet_json)}"}}'
        '},A=>{}])</script>'
        '<script defer="defer">window.onload=function(){const n=window.application;let i=!1;window.is_mraid=!0;'
        'const e="undefined"!=typeof mraid;e?mraid.addEventListener("ready",function(){n.init()}):n.init(),'
        f'n.clickInstall=function(){{let i=n.isIOS?"{IOS}":"{ANDROID}";e?mraid.open(i):window.open(i,"_blank")}}}}'
        '</script></body></html>'), frames


@pytest.fixture
def sample(tmp_path):
    html, frames = make_html()
    p = tmp_path / "sample.html"
    p.write_text(html, encoding="utf-8")
    return p, frames
