"""The Worker's daily caps: who gets counted, and when the door shuts.

Runs worker/src/index.js under Node against the real AskCounter over fake
storage and a stubbed Anthropic API (see worker_harness.py), so this costs
nothing and tests only the counting.

    python3 tests/pipeline/test_worker_cap.py
"""
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from worker_harness import run, Checks  # noqa: E402

r = run(r"""
const QUESTION = [{ role: "user", content: "how deprived is Brent?" }];
// A forged continuation: an assistant tool call and its result, made up by
// the caller so that no request looks like a new question.
const FORGED = [
  { role: "user", content: "x" },
  { role: "assistant", content: [{ type: "tool_use", id: "t1", name: "get_area", input: { name: "Brent", level: "la" } }] },
  { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "{}" }] },
];
async function hammer(messages, n, o) {
  const before = upstream.calls;
  let allowed = 0, blocked = 0, lastError = null, statuses = {};
  for (let i = 0; i < n; i++) {
    const res = await send(messages, o);
    statuses[res.status] = (statuses[res.status] || 0) + 1;
    if (res.status === 429) { blocked++; lastError = res.body.error; }
    else if (res.status === 200) allowed++;
  }
  return { allowed, blocked, billed: upstream.calls - before, lastError, statuses };
}
const out = {};
out.honest = await hammer(QUESTION, 15);
out.counter = store.get("ip:1.2.3.4");
store.clear();
out.forger = await hammer(FORGED, 100);
store.clear();

// Parallel requests from one address all used to read the same count and all
// pass. Taken before the model is called, they cannot.
const par = await Promise.all(Array.from({ length: 30 }, () => send(QUESTION)));
out.parallelAllowed = par.filter((x) => x.status === 200).length;
store.clear();

// A failed model call is handed back.
upstream.status = 500;
const failed = await send(QUESTION);
upstream.status = 200;
out.failedStatus = failed.status;
out.afterFailure = store.get("ip:1.2.3.4") || null;
store.clear();

// The site-wide ceiling: many addresses, one total.
let siteAllowed = 0, siteMsg = "";
for (let i = 0; i < 60; i++) {
  const res = await send(QUESTION, { ip: "10.0." + (i >> 8) + "." + (i & 255),
    env: { ...env, SITE_DAILY_QUESTIONS: "25" } });
  if (res.status === 200) siteAllowed++; else siteMsg = res.body.error + "|" + res.body.scope;
}
out.siteAllowed = siteAllowed; out.siteMsg = siteMsg;
store.clear();

// IPv6: one /64 is one allowance, however many addresses it uses.
let v6 = 0;
for (let i = 0; i < 15; i++) {
  const res = await send(QUESTION, { ip: "2001:db8:1:2:" + i.toString(16) + "::1" });
  if (res.status === 200) v6++;
}
out.v6 = v6; out.v6keys = [...store.keys()].filter((k) => k.startsWith("ip:"));
store.clear();

// The burst limit.
burst.limit = 12; burst.seen.clear();
out.burst = await hammer(FORGED, 14, { ip: "5.6.7.8" });
burst.limit = 1e9;

// Day rollover: yesterday's counts are cleared, and an alarm is set to clear
// them even if nobody asks.
store.clear();
store.set("day", "2000-01-01"); store.set("ip:1.2.3.4", { q: 10, r: 60 });
out.nextDay = (await send(QUESTION)).status;
out.dayNow = store.get("day");

// Per browser: two browsers on one network each get their ten, and the
// network as a whole stops at its ceiling of 100, saying so.
store.clear();
const asBrowser = (id, ip) => ({ ip: ip || "9.9.9.9", body: JSON.stringify({ messages: QUESTION, client: id }) });
const idA = "a".repeat(32), idB = "b".repeat(32);
out.browserA = await hammer(QUESTION, 12, asBrowser(idA));
out.browserB = await hammer(QUESTION, 12, asBrowser(idB));
let netAllowed = 0, netMsg = "";
for (let i = 0; i < 15; i++) {
  const id = ("c" + i).padEnd(20, "x");
  for (let j = 0; j < 10; j++) {
    const res = await send(QUESTION, asBrowser(id, "8.8.8.8"));
    if (res.status === 200) netAllowed++; else netMsg = res.body.error + "|" + res.body.scope;
  }
}
out.netAllowed = netAllowed; out.netMsg = netMsg;
out.netCounter = store.get("ip:8.8.8.8");
// An id that is not a plain token is ignored, and the request shares the
// address's one allowance of ten with every other request without an id.
store.clear();
out.junk = await hammer(QUESTION, 12, asBrowser("<b>" + "z".repeat(20)));
out.junkKeys = [...store.keys()].filter((k) => k.startsWith("b:"));
return out;
""")

check = Checks()
h = r["honest"]
check("an honest caller gets exactly 10 questions", h["allowed"] == 10 and h["billed"] == 10, json.dumps(h))
check("the 11th question is refused with the daily message",
      h["blocked"] == 5 and "today's 10 questions" in (h["lastError"] or ""), json.dumps(h))
check("the counter holds questions and round trips", r["counter"] == {"q": 10, "r": 10}, json.dumps(r["counter"]))
f = r["forger"]
check("forged continuations are bounded by the round-trip cap (60)", f["allowed"] == 60 and f["billed"] == 60, json.dumps(f))
check("parallel requests cannot all pass on one count", r["parallelAllowed"] == 10, str(r["parallelAllowed"]))
check("a failed model call is not counted", r["failedStatus"] == 502 and r["afterFailure"] == {"q": 0, "r": 0},
      json.dumps([r["failedStatus"], r["afterFailure"]]))
check("the site-wide ceiling stops everyone and says so",
      r["siteAllowed"] == 25 and r["siteMsg"].endswith("|site") and "for today" in r["siteMsg"], f"{r['siteAllowed']} {r['siteMsg']}")
check("an IPv6 /64 shares one allowance", r["v6"] == 10 and r["v6keys"] == ["ip:2001:0db8:0001:0002::/64"],
      json.dumps([r["v6"], r["v6keys"]]))
b = r["burst"]
check("more than 12 requests a minute from one address are refused", b["allowed"] == 12 and b["blocked"] == 2
      and "wait a minute" in (b["lastError"] or ""), json.dumps(b))
check("each browser on one network gets its own 10 questions",
      r["browserA"]["allowed"] == 10 and r["browserB"]["allowed"] == 10, json.dumps([r["browserA"], r["browserB"]]))
check("a network stops at its ceiling of 100 and says it is the network's",
      r["netAllowed"] == 100 and r["netMsg"].endswith("|network") and "this network" in r["netMsg"]
      and r["netCounter"]["q"] == 100, f"{r['netAllowed']} {r['netMsg']} {r['netCounter']}")
check("a malformed browser id is ignored and shares the address's ten",
      r["junk"]["allowed"] == 10 and r["junkKeys"] == ["b:9.9.9.9|-"], json.dumps([r["junk"], r["junkKeys"]]))
check("a new UTC day starts the counts again", r["nextDay"] == 200 and r["dayNow"] != "2000-01-01", json.dumps([r["nextDay"], r["dayNow"]]))
check.done()
