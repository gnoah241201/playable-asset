"""Ad-network adapters.

Source builds target AppLovin (MRAID): a `window.onload=function(){...}` bootstrap that
initialises `window.application` and opens the store with `mraid.open(...)`.
Each adapter rewrites that bootstrap for one network.
"""
import re

from .errors import ConversionError

BOOTSTRAP_RE = re.compile(r'<script[^>]*>\s*window\.onload=function\(\)\{.*?</script>', re.S)
STORE_RE = re.compile(r'isIOS\?"([^"]+)":"([^"]+)"')
AD_NETWORK_RE = re.compile(r'adNetwork:"([^"]*)"')

MINTEGRAL_BRIDGE = (
    '<script defer="defer">window.onload=function(){'
    'var app=window.application,ended=false;window.is_mraid=!0;'
    'function end(){if(!ended){ended=true;window.gameEnd&&window.gameEnd();}}'
    'app.playableFinished=function(){end();};'
    'app.clickInstall=function(){end();window.install&&window.install();};'
    'window.gameStart=function(){if(!app.soundMuted&&app.sound)app.sound.unmuteAll();};'
    'window.gameClose=function(){app.sound&&app.sound.muteAll();};'
    'Promise.resolve(app.init()).then(function(){window.gameReady&&window.gameReady();});'
    '}</script>')


def detect_network(html):
    if "window.gameReady" in html and "window.install" in html:
        return "mintegral"
    m = AD_NETWORK_RE.search(html)
    if m:
        return m.group(1)
    if "mraid" in html:
        return "mraid"
    return None


def get_store_links(html):
    m = STORE_RE.search(html)
    return {"ios": m.group(1), "android": m.group(2)} if m else None


def set_store_links(html, ios=None, android=None):
    m = STORE_RE.search(html)
    if not m:
        raise ConversionError("Store links (isIOS?\"...\":\"...\") not found in the bootstrap script.")
    new = 'isIOS?"%s":"%s"' % (ios or m.group(1), android or m.group(2))
    return html[:m.start()] + new + html[m.end():]


def _set_ad_network(html, name):
    html = html.replace(",window.is_applovin=!0", "").replace("window.is_applovin=!0", "")
    return AD_NETWORK_RE.sub(f'adNetwork:"{name}"', html, count=1)


def to_applovin(html):
    if detect_network(html) == "mintegral":
        raise ConversionError("Input is already a Mintegral build; convert from the AppLovin/MRAID source instead.")
    if not BOOTSTRAP_RE.search(html) or "mraid" not in html:
        raise ConversionError("MRAID bootstrap (window.onload=function(){...mraid...}) not found.")
    return html


def to_mintegral(html):
    if detect_network(html) == "mintegral":
        return html
    m = BOOTSTRAP_RE.search(html)
    if not m or "mraid" not in m.group(0):
        raise ConversionError("MRAID bootstrap (window.onload=function(){...mraid...}) not found; "
                              "this build uses an unknown startup sequence.")
    if "window.application" not in m.group(0):
        raise ConversionError("Bootstrap does not use window.application; unknown playable template.")
    html = html[:m.start()] + MINTEGRAL_BRIDGE + html[m.end():]
    return _set_ad_network(html, "mintegral")


ADAPTERS = {"applovin": to_applovin, "mintegral": to_mintegral}
OUTPUT_FORMAT = {"applovin": "html", "mintegral": "zip"}  # mintegral: zip with a single index.html
SIZE_LIMIT_MB = {"applovin": 5, "mintegral": 5}
