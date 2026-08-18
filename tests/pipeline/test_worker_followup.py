"""One free reply per question: who gets counted once the assistant may ask.

The assistant is allowed one clarifying question per query, and the reader's
answer to it is not counted against their ten a day. Before that, asking cost
the reader a question to answer, which made asking worth avoiding and was why
the assistant had been told not to.

The rule has to hold on the shape of the conversation alone, because the client
supplies the whole message array and can say anything about itself. Same
approach as test_worker_cap: no Node here, but the Worker is plain JavaScript
and Chrome runs it, against a fake KV and a stubbed API so nothing is billed.
"""
import json
import pathlib

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

SRC = pathlib.Path(__file__).resolve().parents[2] / "worker" / "src" / "index.js"
code = SRC.read_text(encoding="utf-8").replace("export default {", "var handler = {")

HARNESS = """
%s

const store = new Map();
const env = {
  ANTHROPIC_API_KEY: "sk-test",
  ALLOWED_ORIGINS: "https://pophealth.uk",
  RATE_LIMIT: {
    get: async (k) => (store.has(k) ? store.get(k) : null),
    put: async (k, v) => { store.set(k, v); },
  },
};
let upstreamCalls = 0;
globalThis.fetch = async () => {
  upstreamCalls++;
  return { ok: true, json: async () => ({ stop_reason: "end_turn", content: [] }) };
};

function req(messages) {
  return {
    method: "POST",
    headers: { get: (h) => (h === "Origin" ? "https://pophealth.uk"
                            : h === "CF-Connecting-IP" ? "1.2.3.4" : null) },
    json: async () => ({ messages }),
  };
}

const typed   = (t) => ({ role: "user", content: t });
const asked   = (t) => ({ role: "assistant", content: [{ type: "text", text: t }] });
const said    = (t) => ({ role: "assistant", content: [{ type: "text", text: t }] });
const worked  = ()  => ({ role: "assistant", content: [
                          { type: "tool_use", id: "t1", name: "get_area", input: {} }] });
const results = ()  => ({ role: "user", content: [
                          { type: "tool_result", tool_use_id: "t1", content: "{}" }] });

const CLARIFY = "Which Church End did you mean, the one in Brent or the one in Barnet?";

// One request against an empty counter. The counter then holds exactly what
// this request cost: 1 if it was charged as a question, 0 if it was free.
async function charged(convo) {
  store.clear();
  const res = await handler.fetch(req(convo), env);
  if (res.status !== 200) return "http " + res.status;
  const raw = [...store.values()][0] || "0,0";
  return parseInt(raw.split(",")[0], 10);
}

// What a whole exchange costs, replayed the way the client sends it: each
// request carries the conversation so far.
//
// Only the prefixes ending in a user message are sent. The client posts when
// it has something of its own to add, a typed question or a batch of tool
// results, and never an array ending in an assistant turn.
async function exchangeCost(steps) {
  store.clear();
  for (let i = 1; i <= steps.length; i++) {
    if (steps[i - 1].role !== "user") continue;
    await handler.fetch(req(steps.slice(0, i)), env);
  }
  const raw = [...store.values()][0] || "0,0";
  return { questions: parseInt(raw.split(",")[0], 10),
           requests: parseInt(raw.split(",")[1], 10) };
}

return (async () => {
  const out = {};

  out.plainQuestion       = await charged([typed("how deprived is Brent?")]);
  out.replyToClarification = await charged([
    typed("tell me about Church End"), asked(CLARIFY), typed("Brent")]);
  out.secondReplySameQuery = await charged([
    typed("tell me about Church End"), asked(CLARIFY), typed("Brent"),
    asked("Which measure, score or decile?"), typed("score")]);
  out.replyAfterToolWork   = await charged([
    typed("how deprived is Brent?"), worked(), results(),
    said("Brent scores 28.4."), typed("and Ealing?")]);
  out.replyAfterStatement  = await charged([
    typed("how deprived is Brent?"), said("Brent scores 28.4."), typed("and Ealing?")]);
  out.toolResultContinuation = await charged([
    typed("how deprived is Brent?"), worked(), results()]);
  out.freeResetsNextQuestion = await charged([
    typed("tell me about Church End"), asked(CLARIFY), typed("Brent"),
    said("Brent scores 28.4."), typed("what about Ealing?"),
    asked("Which measure?"), typed("score")]);

  // A whole clarified exchange, priced end to end.
  out.clarifiedExchange = await exchangeCost([
    typed("tell me about Church End"), asked(CLARIFY), typed("Brent"),
    worked(), results()]);

  return JSON.stringify(out);
})();
""" % code

opts = Options()
opts.add_argument("--headless=new")
d = webdriver.Chrome(options=opts)
try:
    d.get("data:text/html,<html><body></body></html>")
    r = json.loads(d.execute_script(HARNESS))
finally:
    d.quit()

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


check("a typed question is counted", r["plainQuestion"] == 1, str(r["plainQuestion"]))
check("the reply to a clarification is free",
      r["replyToClarification"] == 0, str(r["replyToClarification"]))
check("a second reply in the same exchange is counted",
      r["secondReplySameQuery"] == 1, str(r["secondReplySameQuery"]))
check("a follow-up after the assistant did the work is counted",
      r["replyAfterToolWork"] == 1, str(r["replyAfterToolWork"]))
check("a follow-up after a statement is counted",
      r["replyAfterStatement"] == 1, str(r["replyAfterStatement"]))
check("tool results are still never counted",
      r["toolResultContinuation"] == 0, str(r["toolResultContinuation"]))
check("a new question brings a fresh free reply with it",
      r["freeResetsNextQuestion"] == 0, str(r["freeResetsNextQuestion"]))
check("a clarified exchange costs one question, not two",
      r["clarifiedExchange"]["questions"] == 1, json.dumps(r["clarifiedExchange"]))

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
raise SystemExit(1 if fails else 0)
