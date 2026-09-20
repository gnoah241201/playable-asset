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
# last resort: any store URL anywhere in the file (works for engines we do not know)
_URL_STOP = "[^\\s\"'`\\\\<>)]+"
ANY_IOS_RE = re.compile(r"https://(?:apps|itunes)\.apple\.com/" + _URL_STOP)
ANY_ANDROID_RE = re.compile(r"https://play\.google\.com/store/apps/" + _URL_STOP)

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


# Engine-agnostic bridge: the playable keeps using MRAID, we translate it to the Mintegral SDK.
# Injected before the game's own scripts so `typeof mraid` is defined when they run.
MINTEGRAL_SHIM = (
    '<script>(function(){var ended=false;'
    'function end(){if(!ended){ended=true;window.gameEnd&&window.gameEnd();}}'
    'window.gameStart=function(){try{window.dispatchEvent(new Event("mtg:start"));}catch(e){}};'
    'window.gameClose=function(){try{window.dispatchEvent(new Event("mtg:close"));}catch(e){}};'
    'var L={};window.mraid={getState:function(){return"default"},isViewable:function(){return true},'
    'getVersion:function(){return"3.0"},getPlacementType:function(){return"interstitial"},'
    'addEventListener:function(e,f){(L[e]=L[e]||[]).push(f);if(e==="ready")setTimeout(f,0);},'
    'removeEventListener:function(e,f){(L[e]||[]).splice((L[e]||[]).indexOf(f),1);},'
    'open:function(u){end();window.install&&window.install();},close:function(){},'
    'expand:function(){},useCustomClose:function(){},setOrientationProperties:function(){}};'
    'window.open=function(u){end();window.install&&window.install();return null;};'
    'window.addEventListener("load",function(){setTimeout(function(){window.gameReady&&window.gameReady();},50);});'
    '})();</script>')


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
    if m:
        return {"ios": m.group(1), "android": m.group(2)}
    ios, android = ANY_IOS_RE.search(html), ANY_ANDROID_RE.search(html)
    if ios or android:
        return {"ios": ios.group(0) if ios else "", "android": android.group(0) if android else ""}
    return None


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
    if "mraid" not in html:
        raise ConversionError("This playable does not use MRAID.")
    return html


def _has_known_bootstrap(html):
    m = BOOTSTRAP_RE.search(html)
    return bool(m) and "mraid" in m.group(0) and "window.application" in m.group(0)


def to_mintegral(html):
    """Tailored bridge for the known template; otherwise a generic MRAID -> Mintegral shim."""
    if detect_network(html) == "mintegral":
        return html
    if _has_known_bootstrap(html):
        m = BOOTSTRAP_RE.search(html)
        html = html[:m.start()] + MINTEGRAL_BRIDGE + html[m.end():]
    elif "mraid" in html:
        m = re.search(r"<head[^>]*>", html, re.I)
        html = (html[:m.end()] + MINTEGRAL_SHIM + html[m.end():]) if m else MINTEGRAL_SHIM + html
    else:
        raise ConversionError("This playable does not use MRAID, so it cannot be converted automatically.")
    return _set_ad_network(html, "mintegral")


ADAPTERS = {"applovin": to_applovin, "mintegral": to_mintegral}
OUTPUT_FORMAT = {"applovin": "html", "mintegral": "zip"}  # mintegral: zip with a single index.html
SIZE_LIMIT_MB = {"applovin": 5, "mintegral": 5}
