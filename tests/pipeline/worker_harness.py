"""Run worker/src/index.js under Node, with fakes for everything it calls.

The Worker is plain JavaScript and Node 22 has the same fetch, Request and
Response it uses, so the module is imported as it is and driven directly. Its
bindings are faked: the daily counter is the real AskCounter class over an
in-memory storage, the burst limiter is a counter per key, and the Anthropic
call is a stub, so nothing is billed and no Cloudflare account is needed.

run(js) executes `js` as the body of an async function with these in scope:

  handler          the Worker's default export
  env              its bindings (ANTHROPIC_API_KEY, ASK_COUNTER, ASK_BURST, ...)
  store            the counter's storage, a Map
  upstream         { calls, bodies, reply }: set reply to change what the
                   stubbed API answers; bodies are the parsed request bodies
  req(messages, o) a request from the map's origin (o.ip, o.origin, o.body)
  send(messages,o) handler.fetch(req(...)) with the body parsed: {status, body}
  burst            { limit }: requests a minute per key before a 429

and returns whatever the function returns, parsed from JSON.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "worker" / "src" / "index.js"

PRELUDE = r"""
import handler, { AskCounter } from "./worker.mjs";

const store = new Map();
let alarm = null;
const storage = {
  get: async (k) => (store.has(k) ? structuredClone(store.get(k)) : undefined),
  put: async (k, v) => {
    if (typeof k === "object") { for (const [a, b] of Object.entries(k)) store.set(a, structuredClone(b)); }
    else store.set(k, structuredClone(v));
  },
  delete: async (k) => store.delete(k),
  deleteAll: async () => { store.clear(); },
  getAlarm: async () => alarm,
  setAlarm: async (t) => { alarm = t; },
};
const counterObject = new AskCounter({ storage }, {});
// A Durable Object runs one request at a time and its storage calls cannot
// interleave (Cloudflare's input gates). The fake queues them to match.
let queue = Promise.resolve();
const serial = (fn) => { const p = queue.then(fn, fn); queue = p.catch(() => {}); return p; };
const burst = { limit: 1e9, seen: new Map() };
const env = {
  ANTHROPIC_API_KEY: "sk-test",
  ALLOWED_ORIGINS: "https://pophealth.uk",
  ASK_COUNTER: {
    idFromName: (n) => n,
    get: () => ({ fetch: (url, init) => serial(() => counterObject.fetch(new Request(url, init))) }),
  },
  ASK_BURST: {
    limit: async ({ key }) => {
      const n = (burst.seen.get(key) || 0) + 1;
      burst.seen.set(key, n);
      return { success: n <= burst.limit };
    },
  },
};
const upstream = { calls: 0, bodies: [], reply: { stop_reason: "end_turn", content: [{ type: "text", text: "ok" }] }, status: 200 };
globalThis.fetch = async (url, init) => {
  upstream.calls++;
  upstream.bodies.push(JSON.parse(init.body));
  const status = upstream.status;
  return new Response(JSON.stringify(upstream.reply), { status, headers: { "content-type": "application/json" } });
};
function req(messages, o = {}) {
  const headers = { "content-type": "application/json", Origin: o.origin || "https://pophealth.uk",
                    "CF-Connecting-IP": o.ip || "1.2.3.4" };
  return new Request("https://worker.test/", { method: o.method || "POST", headers,
    body: o.method === "GET" ? undefined : (o.body !== undefined ? o.body : JSON.stringify({ messages })) });
}
async function send(messages, o) {
  const res = await handler.fetch(req(messages, o), o && o.env ? o.env : env);
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  return { status: res.status, body, cors: res.headers.get("Access-Control-Allow-Origin") };
}
const silent = console.log; console.log = () => {};
"""


def run(js):
    node = shutil.which("node")
    if not node:
        raise SystemExit("node is not installed; the Worker tests need Node 18 or later")
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "worker.mjs").write_text(SRC.read_text(encoding="utf-8"), encoding="utf-8")
        (d / "main.mjs").write_text(
            PRELUDE + "\nconst __out = await (async () => {\n" + js + "\n})();\n"
            "process.stdout.write(JSON.stringify(__out));\n",
            encoding="utf-8")
        p = subprocess.run([node, str(d / "main.mjs")], capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise SystemExit("node failed:\n" + p.stderr[-4000:])
        return json.loads(p.stdout)


class Checks:
    def __init__(self):
        self.fails = []

    def __call__(self, name, ok, detail=""):
        print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail and not ok else ""))
        if not ok:
            self.fails.append(name)

    def done(self):
        print()
        print(("FAILED: " + "; ".join(self.fails)) if self.fails else "all checks passed")
        raise SystemExit(1 if self.fails else 0)
