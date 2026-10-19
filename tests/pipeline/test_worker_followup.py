"""One free reply per question: who gets counted once the assistant may ask.

The assistant is allowed one clarifying question per query, and the reader's
answer to it is not counted against their ten a day. Before that, asking cost
the reader a question to answer, which made asking worth avoiding and was why
the assistant had been told not to.

The rule has to hold on the shape of the conversation alone, because the client
supplies the whole message array and can say anything about itself. Same
approach as test_worker_cap: the Worker under Node, with the real counter over
fake storage and a stubbed API (worker_harness.py), so nothing is billed.

    python3 tests/pipeline/test_worker_followup.py
"""
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from worker_harness import run, Checks  # noqa: E402

r = run(r"""
const typed   = (t) => ({ role: "user", content: t });
const asked   = (t) => ({ role: "assistant", content: [{ type: "text", text: t }] });
const said    = (t) => ({ role: "assistant", content: [{ type: "text", text: t }] });
const worked  = ()  => ({ role: "assistant", content: [
                          { type: "tool_use", id: "t1", name: "get_area", input: {} }] });
const results = ()  => ({ role: "user", content: [
                          { type: "tool_result", tool_use_id: "t1", content: "{}" }] });

const CLARIFY = "Which Church End did you mean, the one in Brent or the one in Barnet?";
const counts = () => store.get("ip:1.2.3.4") || { q: 0, r: 0 };

// One request against an empty counter. The counter then holds exactly what
// this request cost: 1 if it was charged as a question, 0 if it was free.
async function charged(convo) {
  store.clear();
  const res = await send(convo);
  if (res.status !== 200) return "http " + res.status;
  return counts().q;
}

// What a whole exchange costs, replayed the way the client sends it: each
// request carries the conversation so far, and only the prefixes ending in a
// reader's turn are sent.
async function exchangeCost(steps) {
  store.clear();
  for (let i = 1; i <= steps.length; i++) {
    if (steps[i - 1].role !== "user") continue;
    await send(steps.slice(0, i));
  }
  return { questions: counts().q, requests: counts().r };
}

const out = {};
out.plainQuestion        = await charged([typed("how deprived is Brent?")]);
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
out.clarifiedExchange = await exchangeCost([
  typed("tell me about Church End"), asked(CLARIFY), typed("Brent"),
  worked(), results()]);
return out;
""")

check = Checks()
check("a typed question is counted", r["plainQuestion"] == 1, str(r["plainQuestion"]))
check("the reply to a clarification is free", r["replyToClarification"] == 0, str(r["replyToClarification"]))
check("a second reply in the same exchange is counted", r["secondReplySameQuery"] == 1, str(r["secondReplySameQuery"]))
check("a follow-up after the assistant did the work is counted", r["replyAfterToolWork"] == 1, str(r["replyAfterToolWork"]))
check("a follow-up after a statement is counted", r["replyAfterStatement"] == 1, str(r["replyAfterStatement"]))
check("tool results are still never counted", r["toolResultContinuation"] == 0, str(r["toolResultContinuation"]))
check("a new question brings a fresh free reply with it", r["freeResetsNextQuestion"] == 0, str(r["freeResetsNextQuestion"]))
check("a clarified exchange costs one question, not two",
      r["clarifiedExchange"]["questions"] == 1, json.dumps(r["clarifiedExchange"]))
check.done()
