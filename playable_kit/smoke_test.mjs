#!/usr/bin/env node
// Runtime smoke test: loads a playable in headless Chrome with a mocked ad SDK.
// Usage: playable-kit smoke <file> ...   or   node smoke_test.mjs <file.html|file.zip> [--network mintegral|applovin] [--seconds 6] [--screenshot out.png]
// Prints one JSON object. Exit 0 = passed, 3 = checks failed, 1 = could not run.
// Requires Node >= 22 (global WebSocket) and Google Chrome / Chromium (set CHROME_PATH if not found).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const argv = process.argv.slice(2);
const opt = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d; };
const input = argv.find(a => !a.startsWith("--") && argv[argv.indexOf(a) - 1]?.startsWith("--") !== true);
const seconds = Number(opt("--seconds", 6));
const shotPath = opt("--screenshot", null);
const out = (obj, code) => { process.stdout.write(JSON.stringify(obj) + "\n"); process.exit(code); };
if (!input) out({ ok: false, error: { code: "usage", message: "usage: smoke_test.mjs <file.html|zip> [--network n]" } }, 2);

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "pk-smoke-"));
let htmlPath = path.resolve(input);
if (input.toLowerCase().endsWith(".zip")) {
  // extract index.html with python's zipfile (always available alongside playable-kit)
  const py = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  execFileSync(py, ["-c", "import sys,zipfile;z=zipfile.ZipFile(sys.argv[1]);n=[x for x in z.namelist() if x.endswith('.html')];" +
    "n=('index.html' if 'index.html' in n else n[0]);open(sys.argv[2],'wb').write(z.read(n))", input, path.join(tmp, "index.html")]);
  htmlPath = path.join(tmp, "index.html");
}
const html = fs.readFileSync(htmlPath, "utf8");
const network = opt("--network", /window\.gameReady/.test(html) ? "mintegral" : "applovin");

const candidates = [process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
const chrome = candidates.find(p => p && fs.existsSync(p));
if (!chrome) out({ ok: false, error: { code: "chrome_not_found", message: "Set CHROME_PATH" } }, 1);

const profile = path.join(tmp, "profile");
const proc = spawn(chrome, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-first-run", "--disable-background-networking",
  "--disable-component-update", "--autoplay-policy=no-user-gesture-required", "about:blank"], { stdio: "ignore" });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const cleanup = () => { try { proc.kill(); } catch {} setTimeout(() => { try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} }, 500); };

let port;
for (let i = 0; i < 60 && !port; i++) {
  try { port = fs.readFileSync(path.join(profile, "DevToolsActivePort"), "utf8").split("\n")[0]; } catch { await sleep(250); }
}
if (!port) { cleanup(); out({ ok: false, error: { code: "chrome_start_failed", message: "DevTools port not found" } }, 1); }
let page;
for (let i = 0; i < 40 && !page; i++) {
  try { page = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(t => t.type === "page"); } catch { await sleep(250); }
}
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0; const pending = new Map(); const exceptions = []; const requests = [];
ws.onmessage = m => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d.result || d); pending.delete(d.id); return; }
  if (d.method === "Runtime.exceptionThrown") exceptions.push(d.params.exceptionDetails.exception?.description?.split("\n")[0] || d.params.exceptionDetails.text);
  if (d.method === "Network.requestWillBeSent") requests.push(d.params.request.url);
};
await new Promise(r => ws.onopen = r);
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });

const MOCK = `(() => {
  const L = window.__pk = { calls: [] }; const rec = k => L.calls.push(k);
  ${network === "mintegral"
    ? `window.gameReady = () => { rec("gameReady"); setTimeout(() => { rec("gameStart"); window.gameStart && window.gameStart(); }, 200); };
       window.gameEnd = () => rec("gameEnd"); window.install = () => rec("install");`
    : `window.mraid = { getState: () => "default", isViewable: () => true, addEventListener: () => {}, removeEventListener: () => {},
         open: u => rec("mraid.open:" + u), getVersion: () => "3.0" };`}
  window.open = u => { rec("window.open:" + u); return null; };
})();`;
await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
await send("Page.addScriptToEvaluateOnNewDocument", { source: MOCK });
await send("Page.navigate", { url: pathToFileURL(htmlPath).href });
await sleep(seconds * 1000);

const evalJs = async expr => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.value;
const state = await evalJs(`({ calls: window.__pk.calls.slice(), canvases: document.querySelectorAll("canvas").length,
  appReady: !!window.application })`);
// trigger the CTA the way the playable would: its own hook if we know it, otherwise MRAID
const ctaPath = await evalJs(`(() => {
  try {
    if (window.application && typeof window.application.clickInstall === "function") { window.application.clickInstall(); return "application.clickInstall"; }
    if (window.mraid && typeof window.mraid.open === "function") { window.mraid.open("https://example.com/smoke-test"); return "mraid.open"; }
    return "no CTA hook found";
  } catch (e) { return "error: " + e.message; }
})()`);
await sleep(300);
const afterCta = await evalJs("window.__pk.calls.slice()");
if (shotPath) {
  const r = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(shotPath, Buffer.from(r.data, "base64"));
}
const external = requests.filter(u => !/^(data:|blob:|file:)/.test(u));
const checks = [
  { id: "no_js_exceptions", pass: exceptions.length === 0, detail: exceptions.slice(0, 5) },
  { id: "canvas_rendered", pass: state.canvases > 0, detail: `${state.canvases} canvas` },
  { id: "no_external_requests", pass: external.length === 0, detail: external.slice(0, 10) },
];
if (network === "mintegral") {
  checks.push({ id: "gameReady_called", pass: state.calls.includes("gameReady"), detail: state.calls });
  checks.push({ id: "cta_calls_install", pass: afterCta.includes("install"), detail: afterCta });
  checks.push({ id: "gameEnd_called", pass: afterCta.includes("gameEnd"), detail: afterCta });
} else {
  checks.push({ id: "cta_calls_mraid_open", pass: afterCta.some(c => c.startsWith("mraid.open:")), detail: afterCta });
}
ws.close(); cleanup();
const ok = checks.every(c => c.pass);
out({ ok, command: "smoke", file: path.resolve(input), network, ctaPath, checks, screenshot: shotPath ? path.resolve(shotPath) : null }, ok ? 0 : 3);
