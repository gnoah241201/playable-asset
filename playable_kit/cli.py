"""Command line interface. With --json every command prints exactly one JSON object to stdout.

Exit codes: 0 success, 1 operation failed (JSON has ok=false + error), 2 bad usage,
            3 validate/smoke ran but at least one check failed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from . import __version__, core
from .errors import PlayableKitError, SmokeUnavailableError
from .networks import ADAPTERS


def _human(cmd, r):
    if cmd == "inspect":
        print(f"supported      : {r['supported']}")
        print(f"ad network     : {r['ad_network']}")
        print(f"store links    : {r['store_links']}")
        print(f"analytics      : {'ENABLED' if r['analytics_enabled'] else 'off/none'}")
        if r.get("supported"):
            print(f"asset modules  : {r['asset_modules']}")
            print(f"spritesheets   : {r['spritesheets']}")
        else:
            print(f"reason         : {r['unsupported_reason']['message']}")
        for u in r["external_urls"]:
            print(f"external url   : {u}")
    elif cmd == "unpack":
        print(f"Unpacked {r['assets']} assets + {r['spritesheets']} spritesheets ({r['frames']} frames) -> {r['workspace']}")
    elif cmd in ("pack", "convert"):
        for o in r.get("outputs", [r]):
            print(f"{o['network']:10s} {o['path']}  ({o['mb']:.2f} MB){'  OVER LIMIT' if o['over_size_limit'] else ''}")
        if cmd == "pack":
            print("repacked spritesheets:", ", ".join(r["changed_spritesheets"]) or "none")
    elif cmd == "smoke":
        print(f"{r['file']}  network={r['network']}  ok={r['ok']}")
        for c in r["checks"]:
            print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['id']}: {c['detail']}")
    elif cmd == "validate":
        print(f"{r['file']}  network={r['network']}  ok={r['ok']}")
        for c in r["checks"]:
            print(f"  [{c['status'].upper():4s}] {c['id']}: {c['message']}")
    for w in r.get("warnings", []):
        print("WARNING:", w.get("code"), w.get("file", ""), w.get("message", ""), file=sys.stderr)


def _smoke(args):
    node = shutil.which("node")
    if not node:
        raise SmokeUnavailableError("Node.js >= 22 is required for smoke tests (not found on PATH)")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_test.mjs")
    cmd = [node, script, os.path.abspath(args.file), "--seconds", str(args.seconds)]
    if args.network:
        cmd += ["--network", args.network]
    if args.screenshot:
        cmd += ["--screenshot", os.path.abspath(args.screenshot)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.seconds + 90)
    try:
        r = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SmokeUnavailableError("smoke test produced no result", stderr=proc.stderr[-2000:])
    if "error" in r:
        raise SmokeUnavailableError(r["error"].get("message", "smoke test failed"), reason=r["error"].get("code"))
    r.pop("command", None)
    return r


def main(argv=None):
    p = argparse.ArgumentParser(prog="playable-kit", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--json", action="store_true", help="machine-readable output (one JSON object on stdout)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect", help="describe a playable .html/.zip without modifying it")
    s.add_argument("file")

    s = sub.add_parser("unpack", help="extract inline assets into an editable workspace")
    s.add_argument("file")
    s.add_argument("-o", "--workspace", required=True)
    s.add_argument("--force", action="store_true", help="overwrite an existing workspace")

    s = sub.add_parser("pack", help="rebuild playables from a workspace")
    s.add_argument("workspace")
    s.add_argument("-o", "--out-dir", help="default: <workspace>/dist")
    s.add_argument("-n", "--network", action="append", choices=sorted(ADAPTERS),
                   help="repeatable; default: all supported networks")
    s.add_argument("--quantize", action="store_true", help="256-colour PNG for repacked spritesheets")

    s = sub.add_parser("convert", help="convert an AppLovin/MRAID build to another network (no asset changes)")
    s.add_argument("file")
    s.add_argument("--to", required=True, choices=sorted(ADAPTERS))
    s.add_argument("-o", "--output")

    s = sub.add_parser("validate", help="static checks against a network's playable requirements")
    s.add_argument("file")
    s.add_argument("-n", "--network", choices=sorted(ADAPTERS), help="default: auto-detect")

    s = sub.add_parser("smoke", help="runtime check in headless Chrome (needs Node >= 22 + Chrome)")
    s.add_argument("file")
    s.add_argument("-n", "--network", choices=sorted(ADAPTERS), help="default: auto-detect")
    s.add_argument("--seconds", type=float, default=6)
    s.add_argument("--screenshot", help="save a PNG screenshot here")

    # also accept --json after the sub-command
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0

    try:
        if args.cmd == "inspect":
            r = core.inspect(args.file)
        elif args.cmd == "unpack":
            r = core.unpack(args.file, args.workspace, force=args.force)
        elif args.cmd == "pack":
            r = core.pack(args.workspace, args.out_dir, tuple(args.network or sorted(ADAPTERS)), args.quantize)
        elif args.cmd == "smoke":
            r = _smoke(args)
        elif args.cmd == "convert":
            r = core.convert(args.file, args.to, args.output)
        else:
            r = core.validate(args.file, args.network)
        code = 3 if args.cmd in ("validate", "smoke") and not r["ok"] else 0
        r = {"ok": code == 0, "command": args.cmd, **{k: v for k, v in r.items() if k != "ok"}}
    except PlayableKitError as e:
        r, code = {"ok": False, "command": args.cmd, "error": e.to_dict()}, 1
    except Exception as e:  # unexpected: still return valid JSON
        r, code = {"ok": False, "command": args.cmd, "error": {"code": "internal_error", "message": repr(e)}}, 1

    if as_json:
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
    elif "error" in r:
        print(f"ERROR [{r['error']['code']}]: {r['error']['message']}", file=sys.stderr)
    else:
        _human(args.cmd, r)
    return code


if __name__ == "__main__":
    sys.exit(main())
