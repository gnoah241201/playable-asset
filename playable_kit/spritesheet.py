"""TexturePacker (JSON hash) spritesheets: explode into full-size frames and repack."""
import io
import json
import math

from PIL import Image

FRAME_KEYS = ("frame", "rotated", "trimmed", "spriteSourceSize", "sourceSize")


def load_json(data):
    return json.loads(data.decode("utf-8-sig"))


def explode(sheet_json, sheet_png):
    """Return (meta, [(frame_name, full_size_RGBA_image, extra_fields)]) in original order.

    Each frame is restored to its untrimmed `sourceSize` and un-rotated, i.e. exactly
    what the game renders.
    """
    doc = load_json(sheet_json)
    if not isinstance(doc.get("frames"), dict):
        raise ValueError("only the TexturePacker JSON *hash* format is supported")
    atlas = Image.open(io.BytesIO(sheet_png)).convert("RGBA")
    out = []
    for name, f in doc["frames"].items():
        fr, ss, src = f["frame"], f["spriteSourceSize"], f["sourceSize"]
        if f.get("rotated"):  # stored rotated 90° clockwise in the atlas
            piece = atlas.crop((fr["x"], fr["y"], fr["x"] + fr["h"], fr["y"] + fr["w"])).rotate(90, expand=True)
        else:
            piece = atlas.crop((fr["x"], fr["y"], fr["x"] + fr["w"], fr["y"] + fr["h"]))
        full = Image.new("RGBA", (src["w"], src["h"]), (0, 0, 0, 0))
        full.paste(piece, (ss["x"], ss["y"]))
        extra = {k: v for k, v in f.items() if k not in FRAME_KEYS}
        out.append((name, full, extra))
    return doc["meta"], out


def _shelf_pack(sizes, pad):
    area = sum((w + pad) * (h + pad) for w, h in sizes.values())
    width = max(max(w for w, _ in sizes.values()) + pad, int(math.sqrt(area) * 1.15))
    pos, x, y, row_h = {}, 0, 0, 0
    for name, (w, h) in sorted(sizes.items(), key=lambda kv: (-kv[1][1], kv[0])):
        if x + w > width:
            x, y, row_h = 0, y + row_h + pad, 0
        pos[name] = (x, y)
        x += w + pad
        row_h = max(row_h, h)
    return width, y + row_h, pos


def encode_png(img, quantize=False):
    if quantize:
        img = img.quantize(256, method=Image.Quantize.FASTOCTREE)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def repack(frames, meta, pad=2, quantize=False):
    """frames: list of (frame_name, full_size_image, extra) in desired order.
    Returns (json_bytes, png_bytes). Frames are alpha-trimmed and never rotated."""
    trimmed, sizes, full = {}, {}, {}
    for name, img, _ in frames:
        img = img.convert("RGBA")
        bbox = img.getchannel("A").getbbox() or (0, 0, 1, 1)
        trimmed[name] = (img.crop(bbox), bbox)
        sizes[name] = (bbox[2] - bbox[0], bbox[3] - bbox[1])
        full[name] = img.size
    W, H, pos = _shelf_pack(sizes, pad)
    atlas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out = {}
    for name, _, extra in frames:
        piece, bbox = trimmed[name]
        x, y = pos[name]
        atlas.paste(piece, (x, y))
        w, h = sizes[name]
        fw, fh = full[name]
        out[name] = {"frame": {"x": x, "y": y, "w": w, "h": h}, "rotated": False,
                     "trimmed": (w, h) != (fw, fh),
                     "spriteSourceSize": {"x": bbox[0], "y": bbox[1], "w": w, "h": h},
                     "sourceSize": {"w": fw, "h": fh}, **extra}
    meta = dict(meta, size={"w": W, "h": H})
    return json.dumps({"frames": out, "meta": meta}, ensure_ascii=False).encode("utf-8"), encode_png(atlas, quantize)
