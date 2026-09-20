"""Locate asset modules that webpack inlined as base64 data URIs.

Supported layout (minified webpack 5 output):

    <ctxId>(a,b,c){const j={"./back.jpg":7019,...}; ...}            # require.context map
    importAll(i(<ctxId>), j.ASSETS_TYPES.<kind>)                     # loader registration
    <moduleId>(a){"use strict";a.exports="data:<mime>;base64,..."}  # the asset itself
"""
import base64
import re
from dataclasses import dataclass

from .errors import UnsupportedBuildError

# Two webpack output shapes are supported:
#   old: 2853(A,I,i){const j={"./x.png":100}}    and  100(A){"use strict";A.exports="data:..."}
#   new: 2853:(M,L,j)=>{var w={"./x.png":100}}   and  100:M=>{"use strict";M.exports="data:..."}
CTX_RE = re.compile(r'(\d+)\s*:?\s*\(\w+,\s*\w+,\s*\w+\)\s*(?:=>)?\s*\{\s*(?:const|var|let)\s+\w+\s*=\s*(\{"\./[^{}]*\})')
IMPORT_RE = re.compile(r'importAll\(\w+\((\d+)\),\s*\w+\.ASSETS_TYPES\.(\w+)\)')
MODULE_RE = re.compile(r'(\d+)\s*:?\s*\(?(\w+)\)?\s*(?:=>)?\s*\{"use strict";\2\.exports='
                       r'"(data:([^;"]+);base64,([A-Za-z0-9+/=]*))"\}')
ENTRY_RE = re.compile(r'"\./([^"]+)":(\d+)')


@dataclass
class AssetModule:
    module_id: str
    kind: str        # images | sounds | spritesheets | spine | atlas | ...
    name: str        # path inside the webpack context, e.g. "stars/star_1.png"
    mime: str
    data: bytes
    span: tuple      # (start, end) of the data URI inside the HTML


def find_asset_modules(html):
    """Return {module_id: AssetModule}. Raises UnsupportedBuildError if nothing is found."""
    ctx_kind = dict(IMPORT_RE.findall(html))
    names = {}
    for m in CTX_RE.finditer(html):
        kind = ctx_kind.get(m.group(1))
        if not kind:
            continue
        for name, mid in ENTRY_RE.findall(m.group(2)):
            names[mid] = (kind, name)
    if not names:
        raise UnsupportedBuildError(
            "No webpack asset contexts registered via importAll(...ASSETS_TYPES...) were found. "
            "Only PixiJS/webpack builds that inline assets as data URIs are supported.")

    modules = {}
    for m in MODULE_RE.finditer(html):
        mid = m.group(1)
        if mid in names:
            kind, name = names[mid]
            modules[mid] = AssetModule(mid, kind, name, m.group(4), base64.b64decode(m.group(5)), m.span(3))
    missing = sorted(set(names) - set(modules))
    if missing:
        raise UnsupportedBuildError(
            f"{len(missing)} asset modules are referenced but not inlined as data URIs "
            "(the build may load assets from external files).",
            missing=[names[m][1] for m in missing])
    return modules


def data_uri(data, mime):
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def strip_data_uris(html):
    return re.sub(r'data:[a-z]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+', "data:", html)
