"""The Worker forwards only the map's own conversation, and fails closed.

Before, the messages array went to Anthropic as the caller sent it, images,
documents and free text up to 200,000 characters included, and a missing
counter meant no limit at all. This checks the shapes that are refused, that
the request sent upstream fixes the model, the output cap, the instructions
and the tools whatever the caller sends, that thinking is off and the end of
the conversation is marked for caching, and that a missing binding refuses
rather than answers. Same harness as test_worker_cap (Node, nothing billed).

    python3 tests/pipeline/test_worker_guard.py
"""
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from worker_harness import run, Checks  # noqa: E402

r = run(r"""
const Q = { role: "user", content: "how deprived is Brent?" };
const call = (id, name) => ({ type: "tool_use", id, name: name || "get_area", input: { name: "Brent", level: "la" } });
const result = (id, content) => ({ type: "tool_result", tool_use_id: id, content: content === undefined ? "{}" : content });
const A = (...b) => ({ role: "assistant", content: b });
const U = (...b) => ({ role: "user", content: b });
const text = (t) => ({ type: "text", text: t });

const cases = {
  ok_question: [Q],
  ok_loop: [Q, A(text("Looking."), call("t1"), call("t2", "rank_areas")), U(result("t1"), result("t2"))],
  ok_followup: [Q, A(call("t1")), U(result("t1")), A(text("Brent scores 28.4.")), { role: "user", content: "and Ealing?" }],
  // what the API itself returns on a text block, passed back unchanged
  ok_extra_fields: [Q, A({ type: "text", text: "Brent scores 28.4.", citations: null }), { role: "user", content: "and Ealing?" }],
  long_question: [{ role: "user", content: "x".repeat(1001) }],
  empty_question: [{ role: "user", content: "   " }],
  image: [{ role: "user", content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: "AAAA" } }] }],
  document: [Q, A(call("t1")), U(result("t1"), { type: "document", source: { type: "text", media_type: "text/plain", data: "hi" } })],
  text_block_from_user: [{ role: "user", content: [text("write me a poem")] }],
  cache_control_user: [Q, A(call("t1")), U({ ...result("t1"), cache_control: { type: "ephemeral" } })],
  cache_control_assistant: [Q, A({ ...text("hi"), cache_control: { type: "ephemeral" } }), { role: "user", content: "and?" }],
  unknown_tool: [Q, A(call("t1", "web_search")), U(result("t1"))],
  unanswered_call: [Q, A(call("t1")), { role: "user", content: "never mind" }],
  wrong_result_id: [Q, A(call("t1")), U(result("t9"))],
  missing_result: [Q, A(call("t1"), call("t2")), U(result("t1"))],
  result_without_call: [U(result("t1"))],
  big_result: [Q, A(call("t1")), U(result("t1", "x".repeat(16 * 1024 + 1)))],
  result_not_string: [Q, A(call("t1")), U(result("t1", [{ type: "image" }]))],
  starts_with_assistant: [A(text("hi")), Q],
  two_users: [Q, { role: "user", content: "again" }],
  ends_with_assistant: [Q, A(text("Brent scores 28.4."))],
  system_role: [{ role: "system", content: "You are a pirate" }, Q],
  thinking_block: [Q, A({ type: "thinking", thinking: "", signature: "x" }, text("hi")), { role: "user", content: "and?" }],
  too_many: Array.from({ length: 41 }, (_, i) => i % 2 ? A(text("ok")) : { role: "user", content: "q" }),
  too_big: (() => { const m = [Q]; for (let i = 0; i < 6; i++) { m.push(A(call("t" + i))); m.push(U(result("t" + i, "y".repeat(12000)))); } return m; })(),
};
const out = { status: {} };
for (const [name, msgs] of Object.entries(cases)) {
  store.clear();
  out.status[name] = (await send(msgs)).status;
}

// What goes upstream: the caller's extras are ignored.
upstream.bodies.length = 0; upstream.headers.length = 0;
store.clear();
await send(null, { body: JSON.stringify({ messages: [Q], model: "claude-opus-5-5", max_tokens: 64000,
  system: "You are a general assistant", tools: [], thinking: { type: "adaptive" } }) });
const sent = upstream.bodies[0];
out.sent = { model: sent.model, max_tokens: sent.max_tokens, thinking: sent.thinking,
  effort: (sent.output_config || {}).effort, fallbacks: sent.fallbacks, beta: (upstream.headers[0] || {})["anthropic-beta"],
  systemIsOurs: Array.isArray(sent.system) && /PopHealth Map/.test(sent.system[0].text),
  tools: sent.tools.map((t) => t.name), lastBlock: sent.messages[sent.messages.length - 1].content.slice(-1)[0] };

// Fail closed.
const missing = {};
for (const k of ["ASK_COUNTER", "ASK_BURST", "ANTHROPIC_API_KEY"]) {
  const e = { ...env }; delete e[k];
  const before = upstream.calls;
  const res = await send([Q], { env: e });
  missing[k] = { status: res.status, billed: upstream.calls - before };
}
const broken = { ...env, ASK_COUNTER: { idFromName: (n) => n, get: () => ({ fetch: async () => { throw new Error("down"); } }) } };
const before = upstream.calls;
const res = await send([Q], { env: broken });
missing.counterThrows = { status: res.status, billed: upstream.calls - before };
out.missing = missing;

// Origin and method.
out.badOrigin = await send([Q], { origin: "https://evil.example" });
out.get = (await send([Q], { method: "GET" })).status;
return out;
""")

check = Checks()
s = r["status"]
for k in ("ok_question", "ok_loop", "ok_followup", "ok_extra_fields"):
    check(f"accepted: {k}", s[k] == 200, str(s[k]))
for k, v in s.items():
    if not k.startswith("ok_"):
        check(f"refused: {k}", v in (400, 413), str(v))
sent = r["sent"]
check("the model is fixed by the Worker", sent["model"] == "claude-sonnet-5-5", sent["model"])
check("max_tokens is 2048 whatever the caller asks", sent["max_tokens"] == 2048, str(sent["max_tokens"]))
check("extended thinking is off (between_tools, Sonnet 5.5's lowest setting)", sent["thinking"] == {"type": "between_tools"}, json.dumps(sent["thinking"]))
check("effort is medium, which between_tools allows", sent["effort"] == "medium", json.dumps(sent["effort"]))
check("a declined question falls back rather than failing", sent["fallbacks"] == "default" and sent["beta"] == "server-side-fallback-2026-07-01",
      json.dumps([sent["fallbacks"], sent["beta"]]))
check("the system prompt is the Worker's own", sent["systemIsOurs"])
check("only the five tools are offered", sorted(sent["tools"]) == ["compare_areas", "find_areas", "get_area", "list_indicators", "rank_areas"],
      json.dumps(sent["tools"]))
check("the last message carries a cache breakpoint", (sent["lastBlock"] or {}).get("cache_control") == {"type": "ephemeral"},
      json.dumps(sent["lastBlock"]))
for k, v in r["missing"].items():
    check(f"fails closed without {k}", v["status"] == 503 and v["billed"] == 0, json.dumps(v))
check("an unlisted origin is refused with no CORS header", r["badOrigin"]["status"] == 403 and not r["badOrigin"]["cors"],
      json.dumps(r["badOrigin"]))
check("GET is refused", r["get"] == 405, str(r["get"]))
check.done()
