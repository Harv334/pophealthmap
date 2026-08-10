/**
 * PopHealth Map AI proxy.
 *
 * Sits between the map and the Anthropic API so the API key never reaches the
 * browser. The browser sends a conversation; this returns the model's reply.
 *
 * The model is deliberately not given the data. It is given tools, and the
 * browser executes them against the JSON it has already loaded. That means
 * every figure in an answer comes from the same numbers the map is drawing,
 * the model cannot invent a statistic, and no dataset has to be uploaded.
 */

const MODEL = "claude-haiku-4-5";
const MAX_TOKENS = 1024;
const DAILY_CAP = 40; // questions per IP, per UTC day

/**
 * Billed calls per IP per day, counting every round trip.
 *
 * DAILY_CAP alone is not enforceable. A question takes several round trips and
 * only the first is counted as a question, which is fine for the real client
 * but not for anyone else: the caller supplies the whole message array, so
 * appending a fabricated tool_result block makes every request look like a
 * continuation and none of them get counted. That is an unauthenticated
 * endpoint spending money with no ceiling.
 *
 * So there are two counters in one value. Questions give the honest user the
 * message they expect, and requests bound what a forger can spend: six round
 * trips per question is more than the client's five-round limit ever needs,
 * and it caps a determined caller at a few pounds a day rather than at
 * whatever the API will sell them.
 */
const REQUEST_CAP = DAILY_CAP * 6;

const SYSTEM_PROMPT = `You are the assistant built into PopHealth Map, a public
population health map of the 33 London local authorities at ward and LSOA level.

You answer questions about this data only. You have no data in your context;
you get it by calling tools, and the page runs them against the exact figures
it is displaying.

Rules that matter:
- Every number you state must come from a tool result. Never estimate,
  interpolate, or recall a figure from memory. If a tool returns nothing, say
  the data is not available rather than guessing.
- Call the tools you need before answering. Prefer one comparison call over
  several single lookups.
- Deprivation runs in opposite directions depending on the measure, and this is
  the single easiest thing here to get wrong. Get it right every time:
    - imd_score and the domain scores (income, employment, health, education,
      crime, barriers, environment): HIGHER means MORE deprived. The most
      deprived areas are direction "highest". Hackney and Newham score around
      31 and are among the most deprived in London. Richmond scores about 9
      and is among the least.
    - imd deciles: LOWER means MORE deprived. Decile 1 is the most deprived
      tenth nationally, decile 10 the least. The most deprived areas are
      direction "lowest".
  Before you rank, decide which of those two you are using, then pick the
  direction to match the question. Asked for the MOST deprived by score, use
  "highest", never "lowest".
- Tool results carry higher_means and results_are, which state what a high
  value means and what the rows you were given actually are. Read them before
  writing your answer and never contradict them. If results_are says "the
  lowest values; higher = more deprived", those rows are the LEAST deprived
  areas, and saying they have the highest scores would be wrong twice over.
- Rates and percentages are not counts. Do not add percentages together.
- Some indicators are published at borough level and repeated for every ward in
  that borough. Tool results mark these, with published_at, with
  borough_level_indicators, or with a caveat. When one appears, say the figure
  is a borough figure. Never present it as a difference between wards, and do
  not rank wards by it: the ordering would be an artefact, not a finding.
- These are area statistics. They describe places, not individuals, and a
  ward-level figure says nothing about any particular person in it.
- If asked something outside this data (medical advice, individual patients,
  anything not in the map) say briefly that it is out of scope and offer what
  the map can answer instead.
- UK English. Be concise: a couple of short paragraphs, or a small table when
  comparing. No preamble.`;

const TOOLS = [
  {
    name: "get_area",
    description:
      "Look up every indicator held for one ward or borough by name. Use for " +
      "'tell me about X' or any question about a single place.",
    input_schema: {
      type: "object",
      properties: {
        name: { type: "string", description: "Ward or borough name, e.g. 'Brent' or 'Church End'" },
        level: { type: "string", enum: ["ward", "borough"], description: "Which geography to search" },
      },
      required: ["name", "level"],
    },
  },
  {
    name: "compare_areas",
    description:
      "Compare two or more wards or boroughs across the indicators given, or " +
      "across headline indicators if none are named.",
    input_schema: {
      type: "object",
      properties: {
        names: { type: "array", items: { type: "string" }, description: "2 to 6 area names" },
        level: { type: "string", enum: ["ward", "borough"] },
        indicators: {
          type: "array",
          items: { type: "string" },
          description: "Optional indicator keys, e.g. imd_score, census_population",
        },
      },
      required: ["names", "level"],
    },
  },
  {
    name: "rank_areas",
    description:
      "Rank areas by one indicator, highest or lowest first. Use for 'which " +
      "ward has the most/least X' and for top-N questions.",
    input_schema: {
      type: "object",
      properties: {
        indicator: { type: "string", description: "Indicator key to rank by" },
        direction: { type: "string", enum: ["highest", "lowest"] },
        n: { type: "integer", description: "How many to return, 1 to 20" },
        level: { type: "string", enum: ["ward", "borough"] },
        within_borough: { type: "string", description: "Optional: restrict to one borough" },
      },
      required: ["indicator", "direction", "n", "level"],
    },
  },
  {
    name: "list_indicators",
    description:
      "List the indicator keys available, with their human labels. Call this " +
      "first if you are unsure what an indicator is called.",
    input_schema: { type: "object", properties: {} },
  },
];

const CORS_BASE = {
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type",
  "Access-Control-Max-Age": "86400",
};

function corsHeaders(request, env) {
  // Locked to the production origins. An unknown origin gets no CORS header at
  // all, so the browser refuses the response.
  const allowed = (env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
  const origin = request.headers.get("Origin") || "";
  const h = { ...CORS_BASE };
  if (allowed.includes(origin)) h["Access-Control-Allow-Origin"] = origin;
  return h;
}

function json(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/**
 * Is this request a fresh question, or the continuation of one?
 *
 * Answering a question takes several round trips: the model asks for a tool,
 * the browser runs it and posts the result back. Those are separate requests
 * here, so counting requests would make a 40 cap mean roughly 10 questions.
 * A continuation is a user message carrying only tool_result blocks, which is
 * something only the client loop sends, never a person typing.
 */
function isNewQuestion(messages) {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "user") return true;
  if (!Array.isArray(last.content)) return true; // plain string: a typed question
  // Note the length check. An empty array satisfies every() vacuously, which
  // would let a caller post {content: []} all day without ever being counted.
  if (last.content.length === 0) return true;
  return !last.content.every((b) => b && b.type === "tool_result");
}

/**
 * Per-IP daily cap, held in KV.
 *
 * Every KV call is wrapped, because on Cloudflare's free plan KV allows 1,000
 * writes a day and this writes once per question. Unwrapped, the 1,001st
 * question of the day would throw inside the request handler and return a 500:
 * the assistant would break outright at exactly the moment it got popular,
 * rather than degrade.
 *
 * A KV failure fails open, matching the unbound-namespace case above. That is a
 * deliberate trade: losing the per-IP cap is recoverable, taking the feature
 * down is not. It does mean KV is not the thing standing between you and a
 * large bill. The spend limit on the Anthropic side is, and it is the one that
 * must actually be set. See worker/README.md.
 */
const NOOP = async () => {};

async function checkDailyCap(env, ip, isQuestion) {
  if (!env.RATE_LIMIT) return { allowed: true, commit: NOOP }; // KV not bound: fail open
  const key = `${new Date().toISOString().slice(0, 10)}:${ip}`;
  let questions = 0;
  let requests = 0;
  try {
    const raw = await env.RATE_LIMIT.get(key);
    if (raw) {
      // "questions,requests". A bare number is the old single-counter format;
      // reading it as questions keeps yesterday's keys from resetting anyone.
      const parts = String(raw).split(",");
      questions = parseInt(parts[0], 10) || 0;
      requests = parts.length > 1 ? parseInt(parts[1], 10) || 0 : questions;
    }
  } catch (e) {
    console.log(`KV read failed, not counting: ${e && e.message}`);
    return { allowed: true, commit: NOOP };
  }

  if (questions >= DAILY_CAP) {
    return { allowed: false, reason: `Daily limit of ${DAILY_CAP} questions reached. Try again tomorrow.` };
  }
  if (requests >= REQUEST_CAP) {
    return { allowed: false, reason: "Daily limit reached. Try again tomorrow." };
  }

  // Counted on the way out rather than here, so a request the model never
  // answered does not burn someone's quota. Anthropic does not bill a call
  // that failed, and neither should we.
  return {
    allowed: true,
    commit: async () => {
      try {
        // 48h TTL comfortably outlives the UTC day the key is for.
        await env.RATE_LIMIT.put(
          key,
          `${questions + (isQuestion ? 1 : 0)},${requests + 1}`,
          { expirationTtl: 172800 },
        );
      } catch (e) {
        // Most likely the free plan's 1,000 writes/day. Answer the question and
        // leave the counter where it is.
        console.log(`KV write failed, cap not incremented: ${e && e.message}`);
      }
    },
  };
}

export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST") return json({ error: "POST only" }, 405, cors);
    if (!cors["Access-Control-Allow-Origin"]) return json({ error: "Origin not allowed" }, 403, cors);
    if (!env.ANTHROPIC_API_KEY) return json({ error: "Worker is not configured" }, 500, cors);

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "Body must be JSON" }, 400, cors);
    }

    const messages = Array.isArray(body.messages) ? body.messages : null;
    if (!messages || !messages.length) return json({ error: "messages[] required" }, 400, cors);
    // Bound what a caller can push through: this is an unauthenticated endpoint.
    if (messages.length > 40) return json({ error: "Conversation too long" }, 400, cors);
    if (JSON.stringify(messages).length > 200_000) return json({ error: "Payload too large" }, 413, cors);

    // Counted after parsing, so the cap can be per question rather than per
    // round trip. A caller already over the cap is refused either way, so a
    // continuation cannot be used to slip past it.
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    const cap = await checkDailyCap(env, ip, isNewQuestion(messages));
    if (!cap.allowed) return json({ error: cap.reason }, 429, cors);

    const upstream = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: MAX_TOKENS,
        system: [{ type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
        tools: TOOLS,
        messages,
      }),
    });

    if (!upstream.ok) {
      const detail = await upstream.text();
      console.log(`anthropic ${upstream.status}: ${detail.slice(0, 400)}`);
      // Do not pass the upstream body back: it can echo request content.
      return json({ error: "The assistant is unavailable right now." }, 502, cors);
    }

    // The call reached the model, so it counts, whatever the client does next.
    await cap.commit();

    const data = await upstream.json();
    return json(data, 200, cors);
  },
};
