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
const DAILY_CAP = 40; // per IP, per UTC day

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
- Deprivation deciles run 1 (most deprived) to 10 (least deprived). Say which
  direction you mean; readers routinely get this backwards.
- Rates and percentages are not counts. Do not add percentages together.
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

async function underDailyCap(env, ip) {
  if (!env.RATE_LIMIT) return true; // KV not bound: fail open rather than break the site
  const key = `${new Date().toISOString().slice(0, 10)}:${ip}`;
  const used = parseInt((await env.RATE_LIMIT.get(key)) || "0", 10);
  if (used >= DAILY_CAP) return false;
  // 48h TTL comfortably outlives the UTC day the key is for.
  await env.RATE_LIMIT.put(key, String(used + 1), { expirationTtl: 172800 });
  return true;
}

export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST") return json({ error: "POST only" }, 405, cors);
    if (!cors["Access-Control-Allow-Origin"]) return json({ error: "Origin not allowed" }, 403, cors);
    if (!env.ANTHROPIC_API_KEY) return json({ error: "Worker is not configured" }, 500, cors);

    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    if (!(await underDailyCap(env, ip))) {
      return json(
        { error: `Daily limit of ${DAILY_CAP} questions reached. Try again tomorrow.` },
        429, cors,
      );
    }

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

    const data = await upstream.json();
    return json(data, 200, cors);
  },
};
