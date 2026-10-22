/**
 * PopHealth Map AI proxy.
 *
 * Sits between the map and the Anthropic API so the API key never reaches the
 * browser. The browser sends a conversation; this returns the model's reply.
 *
 * The model is deliberately not given the data. It is given tools, and the
 * browser executes them against the JSON it has already loaded. That means
 * the figures it is handed are the same numbers the map is drawing and no
 * dataset has to be uploaded. It does not guarantee every number in an answer
 * is one of them: the system prompt tells the model to use only looked-up
 * figures, but it can still derive or misstate one.
 */

/**
 * Sonnet rather than Haiku.
 *
 * Haiku kept getting the deprivation scale wrong: it inverted IMD scores on the
 * very first live question, and after that was corrected it called a mean
 * decile of 3 "the most deprived tenth". Each fix came from a test that
 * happened to be run, which is a poor way to reach a tool that publishes
 * figures about real places.
 *
 * Roughly four times the price per question, which against the spend limit is
 * not the deciding factor. It also puts the system block over Sonnet's 1,024
 * token caching minimum, where Haiku's 2,048 left it just short, so the cached
 * reads claw some of that back.
 */
const MODEL = "claude-sonnet-5-5";

/**
 * Room for the longest answers, with extended thinking switched off.
 *
 * Thinking shares max_tokens with the answer. At 1,024 that left comparisons,
 * the longest answers, cut off mid-figure, so thinking is off in the request
 * below, which suits a model whose job is to look figures up and report them,
 * and the cap is doubled. The panel still says so when an answer is cut short.
 *
 * Sonnet 5.5 refuses thinking {type: "disabled"}; its lowest setting is
 * "between_tools", which does no extended thinking and returns any short
 * progress notes it writes between tool calls as thinking blocks. The panel
 * shows only text and keeps only text and tool calls in the history it sends
 * back (keepBlocks in assistant.js), and this Worker refuses anything else,
 * so those notes are never replayed. That also keeps the panel's history
 * trimming (stubbed tool results, dropped questions) clear of the API's check
 * that earlier turns are unchanged, which applies to replayed thinking blocks.
 * between_tools needs effort "high" or below; "medium" is the suggested start
 * for multistep tool use.
 */
const MAX_TOKENS = 2048;
const THINKING = { type: "between_tools" };
const EFFORT = "medium";
const DAILY_CAP = 10; // questions per browser, per UTC day

/**
 * Questions per network (IP address, or IPv6 /64) per UTC day.
 *
 * The ten used to be counted per address, and large NHS trusts and councils
 * send their staff out through a few shared addresses, so the first ten
 * people in an organisation used up Ask for everyone else there. The ten are
 * now counted per browser, by a random id the panel keeps in localStorage and
 * sends with each request, and the address only has this higher ceiling. A
 * caller who invents a new id each time gets no more than this from one
 * network, which the site-wide ceiling bounds in turn. A request with no id
 * (a page cached from before) shares one allowance of ten per address, as
 * before.
 */
const NETWORK_CAP = 100;

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
 * So there are two counters per address. Questions give the honest user the
 * message they expect, and requests bound what a forger can spend: six round
 * trips per question is more than the client's five-round limit ever needs,
 * and it caps a determined caller at a few pounds a day rather than at
 * whatever the API will sell them.
 */
const REQUEST_CAP = DAILY_CAP * 6;
const NETWORK_REQUEST_CAP = NETWORK_CAP * 6;

/**
 * A ceiling for the whole site, per UTC day.
 *
 * The per-address caps stop one person; they do not stop many addresses, or
 * one person with many. These bound the day's total, and when they are
 * reached Ask stops for everyone until midnight UTC rather than spending on.
 * SITE_DAILY_QUESTIONS and SITE_DAILY_REQUESTS in wrangler.toml override them.
 */
const SITE_QUESTION_CAP = 50;
const SITE_REQUEST_CAP = 200;

/**
 * What a conversation may contain. The browser's history fits well inside
 * these (data/map/assistant.js trims it to stay so); anything outside them is
 * not the map talking.
 */
const LIMITS = {
  messages: 40,         // turns in one conversation
  payload: 64 * 1024,   // characters of JSON for the whole messages array
  typed: 1000,          // characters in a typed question
  toolResult: 16 * 1024, // characters in one tool_result
  assistantText: 12000, // characters in one assistant text block
  toolInput: 2000,      // characters of JSON in one tool call's input
};

const SYSTEM_PROMPT = `You are the assistant built into PopHealth Map, a public
population health map of England. Each map shows one English region, London or
one of the other eight, from whole local authorities down to small areas.

You answer questions about this data only. You have no data in your context;
you get it by calling tools, and the page runs them against the exact figures
it is displaying.

Which region you are answering about:
- Every tool result starts with region, the English region of the map the
  reader has open, for example "region": "North East". list_indicators also
  gives la_count and ward_count, the number of LAs and wards on that map. Read
  the region from the tool results, never assume it is London, and name it in
  the answer where it matters, for example "the most deprived of the 12 LAs in
  the North East".
- The tools hold only that region's areas, so every ranking and comparison is
  within it. Do not compare with other regions or with England as a whole
  unless a tool result does so itself: an IMD decile, for example, is a tenth
  of England.
- If the reader asks about a place in another region, the tools will not find
  it. Say it is not on this map, name the region this map covers, and suggest
  they open that region's map.
- The place names in these instructions are examples from the London map. They
  show how the tools behave, not places every map has.

Rules that matter:
- Every number you state must come from a tool result. Never estimate,
  interpolate, or recall a figure from memory. If a tool returns nothing, say
  the data is not available rather than guessing.
- Call the tools you need before answering. Prefer one comparison call over
  several single lookups.
- Search with the reader's own words. Pass the place name they typed to the
  tool exactly as they wrote it, and never substitute one that looks or sounds
  similar. "Church End" is not "Church Street". Answering about the second
  reports real figures, correctly, about a place nobody asked about, and that
  is the worst thing you can do here: it is wrong in a way that looks right.
  The tools do the matching and they are good at it. On the London map, for
  example, "Church End" finds Finchley Church End in Barnet on its own.
- The tools tell you when a name is not enough, and you must use what they say
  rather than work around it. "No ward found matching X" means there is no
  such ward: say so, name what you searched for, and stop. Do not answer about
  somewhere else instead.
- Prefer answering to asking whenever the question has a reasonable reading.
  A loose word like "deprived" or "unhealthy", a place with no indicator named,
  or an indicator with no place named are all answerable: pick the closest
  indicator and say which you picked, give the headline figures, or rank the
  region's areas by it. Name the reading in a short clause inside the answer rather than
  asking first.
- You may ask one clarifying question per query, and the reader's answer is
  free: a reply to a question you asked is not counted against their
  ${DAILY_CAP} a day, so asking costs them nothing but the time.
  The case it is for is "Several areas match", which arrives with a candidates
  list. Give them that list and ask which they meant: the London map, for
  example, has two Kilburns, in Brent and in Camden, and picking one for them
  would be a guess about the place itself. Ask once, name the candidates, and do not ask twice: a second
  question in the same exchange is charged to them, so answer with your best
  reading instead.
- Do not close with an offer of more work. No "would you like me to", no "I can
  also show you", no list of suggested next questions. That is not a
  clarification, it is a charge for something they did not ask for. End on the
  last figure, or on the caveat that qualifies it.
- Deprivation runs in opposite directions depending on the measure, and this is
  the single easiest thing here to get wrong. Get it right every time:
    - imd_score and the domain scores (income, employment, health, education,
      crime, barriers, environment): HIGHER means MORE deprived. The most
      deprived areas are direction "highest". Of two areas scoring 31 and 9,
      the one on 31 is far more deprived.
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
- imd_decile_mean is an average of deciles, not a decile. A value of 3 means the
  typical LSOA there sits in the third decile, among roughly the 30 per cent
  most deprived nationally. Do not say an area is "in the most deprived tenth"
  unless the value is 1. Getting this wrong overstates deprivation, which is
  the worst direction to be wrong in for this data.
- Rates and percentages are not counts. Do not add percentages together.
- Each figure in a tool result comes as v (the value) with its label, unit and
  period. Use the label for what the figure is, not a guess from the key.
  Give the period each figure covers, as the tool states it, the first time
  you use it, for example "84.3 years (2019 to 2023)".
- The map calls the local authority level "LA", whatever kind of council it
  is: a London borough, a metropolitan borough, a unitary authority or a
  district. Use "LA" in answers, introducing it once as "local authority
  (LA)", rather than "borough" or "council area".
- The tools read five levels. From the largest: LA; ward; MSOA, about 8,000
  people; LSOA, about 1,500 people; output area (oa), about 300 people or 125
  households. Use ward or LA unless the question asks for smaller areas or for
  detail inside a ward or LA, such as "which LSOAs in this ward are most
  deprived" (rank_areas at level lsoa with within_ward). Deprivation (IMD) is
  published by LSOA, and ward and LA figures summarise it. Output areas have
  only Census 2021 figures and only codes, not names, and every output area
  lookup or ranking needs within_la or within_ward. Not every indicator exists
  at every level, and list_indicators says which. Not every map has every
  level either: when a tool says a level is not available on this map, say so
  and offer the next level up.
- Always say which level a figure is for, and place a small area by the parents
  the tool gives, for example "LSOA Brent 012A, in Wembley Hill ward, Brent"
  on the London map.
  Pass on the caveat a small-area result carries: figures for so few people
  move a lot.
- Some indicators are published for the whole LA and repeated for every ward in
  it. Tool results mark these, with la_only, with la_level_indicators, or
  with a caveat. When one appears, say the figure is an LA figure. Never
  present it as a difference between wards, and do not rank wards by it: the
  ordering would be an artefact, not a finding.
- These are area statistics. They describe places, not individuals, and a
  ward-level figure says nothing about any particular person in it.
- If asked something outside this data (medical advice, individual patients,
  anything not in the map) say briefly that it is out of scope and offer what
  the map can answer instead.
- UK English. Be concise: a couple of short paragraphs, or a small table when
  comparing. No preamble.`;

/**
 * The levels the tools can read, and the two narrowing fields, shared by all
 * three area tools so they are described the same way everywhere.
 */
const LEVEL = {
  type: "string",
  enum: ["ward", "la", "msoa", "lsoa", "oa"],
  description:
    "ward; la for a local authority (LA); msoa (about 8,000 people); lsoa (about 1,500 people); " +
    "oa for an output area (about 300 people, Census 2021 figures only)",
};
const WITHIN_LA = { type: "string", description: "Optional: restrict to one LA (local authority), by name or code" };
const WITHIN_WARD = {
  type: "string",
  description: "Optional: restrict to one ward, for levels lsoa and oa. Output areas need this or within_la",
};

const TOOLS = [
  {
    name: "get_area",
    description:
      "Look up the indicators held for one area by name or code: a ward, an LA, an MSOA, an LSOA " +
      "or an output area. Use for 'tell me about X' or any question about a single place. Name the " +
      "indicators you need when the question is about particular ones; leave them out for a full " +
      "profile. An output area has a code but no name, and is found within an LA or ward. An LSOA's " +
      "result lists its output area codes.",
    input_schema: {
      type: "object",
      properties: {
        name: {
          type: "string",
          description: "Area name or code, as the reader wrote it: a ward or LA name, an MSOA or LSOA name " +
            "(the LA's name and a number, with a letter for an LSOA), or an ONS code such as E01000570",
        },
        level: LEVEL,
        within_la: WITHIN_LA,
        within_ward: WITHIN_WARD,
        indicators: {
          type: "array",
          items: { type: "string" },
          description: "Optional indicator keys to return, e.g. ft_life_expectancy_female; all of them if left out",
        },
      },
      required: ["name", "level"],
    },
  },
  {
    name: "compare_areas",
    description:
      "Compare two or more areas at one level across the indicators given, or " +
      "across headline indicators if none are named.",
    input_schema: {
      type: "object",
      properties: {
        names: { type: "array", items: { type: "string" }, description: "2 to 6 area names or codes" },
        level: LEVEL,
        within_la: WITHIN_LA,
        within_ward: WITHIN_WARD,
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
      "ward has the most/least X', for top-N questions, and for the small areas " +
      "inside an LA or ward, such as the most deprived LSOAs in a ward.",
    input_schema: {
      type: "object",
      properties: {
        indicator: { type: "string", description: "Indicator key to rank by" },
        direction: { type: "string", enum: ["highest", "lowest"] },
        n: { type: "integer", description: "How many to return, 1 to 20" },
        level: LEVEL,
        within_la: WITHIN_LA,
        within_ward: WITHIN_WARD,
      },
      required: ["indicator", "direction", "n", "level"],
    },
  },
  {
    name: "list_indicators",
    description:
      "List the indicator keys available, with their human labels, grouped by the " +
      "levels each is available at. Call this first if you are unsure what an " +
      "indicator is called or whether it exists at a level.",
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
 * A continuation posted by the client loop, rather than something typed.
 *
 * Answering one question takes several round trips: the model asks for a tool,
 * the browser runs it and posts the result back. A user message carrying only
 * tool_result blocks is one of those. Only the client loop sends them, never a
 * person, and they are never counted.
 */
function isToolResultOnly(msg) {
  if (!msg || msg.role !== "user") return false;
  if (!Array.isArray(msg.content)) return false; // plain string: a typed question
  // Note the length check. An empty array satisfies every() vacuously, which
  // would let a caller post {content: []} all day without ever being counted.
  if (msg.content.length === 0) return false;
  return msg.content.every((b) => b && b.type === "tool_result");
}

/**
 * Did the assistant's turn end by asking the reader something?
 *
 * Text only and ending in a question mark. A turn carrying tool_use did work
 * rather than asked, and is the middle of an answer, not the end of one.
 */
function endsWithClarification(msg) {
  if (!msg || msg.role !== "assistant") return false;
  const blocks = Array.isArray(msg.content) ? msg.content : [];
  if (!blocks.length) return false;
  if (blocks.some((b) => b && b.type === "tool_use")) return false;
  const text = blocks
    .filter((b) => b && b.type === "text")
    .map((b) => String(b.text || ""))
    .join(" ")
    .trim();
  return text.endsWith("?");
}

/**
 * Is this request a fresh question, or something already paid for?
 *
 * Three kinds of user message arrive here.
 *
 * A tool_result continuation is the client loop posting back what a tool
 * returned. Answering one question takes several of these, so counting them
 * would make a cap of 10 mean two or three questions while the panel promises
 * ten. Never counted.
 *
 * A reply to a question the assistant asked. The assistant is allowed to ask
 * for a clarification, and charging for the answer was the reason it was told
 * not to: a reader who asked about Church End, was asked which one, and said
 * "Brent" had spent two of their ten to ask one question. One such reply per
 * question is free, so clarifying costs the reader nothing and the assistant
 * can ask when asking genuinely helps.
 *
 * Anything else typed is a new question and is counted.
 *
 * The whole array is replayed rather than only the last exchange, because the
 * free reply has to be once per question and not once per message. Replaying
 * also means the decision cannot be moved by anything the client says about
 * itself: it falls out of the shape of the conversation.
 *
 * A forger can fabricate an assistant turn ending in a question mark and make
 * every second message free. That halves questions per pound, not the money:
 * REQUEST_CAP counts round trips, not questions, so the ceiling on what any
 * one caller can spend in a day is exactly where it was.
 */
function isNewQuestion(messages) {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "user") return true;
  if (isToolResultOnly(last)) return false;

  let charge = true;
  let freeUsed = false; // has this question already had its one free reply?
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (!m || m.role !== "user" || isToolResultOnly(m)) continue;
    if (!freeUsed && endsWithClarification(messages[i - 1])) {
      charge = false;
      freeUsed = true;
    } else {
      charge = true;
      freeUsed = false; // a new question brings its own free reply with it
    }
  }
  return charge;
}

/**
 * Check a conversation before it is forwarded, and rebuild it from what passed.
 *
 * The caller supplies the whole messages array, and this endpoint has no login,
 * so without this anyone could post any conversation at all, images and
 * documents included, and use the Worker as a free general-purpose model under
 * this site's name. A conversation from the map has one shape, and only that
 * shape is accepted:
 *
 *   - turns alternate, starting and ending with the reader;
 *   - a typed question is a plain string of at most LIMITS.typed characters;
 *   - an assistant turn holds only text and calls to the four tools;
 *   - a reader turn made of blocks holds only tool_result blocks, one for each
 *     tool call in the turn before it, each a string of at most
 *     LIMITS.toolResult characters;
 *   - the whole array is at most LIMITS.payload characters of JSON.
 *
 * Images, documents, cache_control and anything else are refused rather than
 * stripped, because the map never sends them. The messages forwarded are
 * rebuilt from the fields checked here, so nothing unchecked rides along.
 */
const TOOL_NAMES = new Set(TOOLS.map((t) => t.name));
const TOOL_ID = /^[A-Za-z0-9_-]{1,100}$/;

function refuse(error, status) {
  return { ok: false, error, status: status || 400 };
}

function isPlainObject(v) {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function validateMessages(messages) {
  if (!Array.isArray(messages) || !messages.length) return refuse("messages[] required");
  if (messages.length > LIMITS.messages) return refuse("Conversation too long", 413);
  let size;
  try {
    size = JSON.stringify(messages).length;
  } catch {
    return refuse("messages[] must be JSON");
  }
  if (size > LIMITS.payload) return refuse("Payload too large", 413);

  const out = [];
  let pending = null; // the tool_use ids the previous assistant turn is waiting on
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    const role = i % 2 === 0 ? "user" : "assistant";
    if (!isPlainObject(m) || m.role !== role) {
      return refuse("Turns must alternate, starting with the reader");
    }
    if (role === "user") {
      if (typeof m.content === "string") {
        if (pending) return refuse("A tool call was left without a result");
        if (!m.content.trim()) return refuse("Empty question");
        if (m.content.length > LIMITS.typed) return refuse("Question too long", 413);
        out.push({ role: "user", content: m.content });
        continue;
      }
      if (!Array.isArray(m.content) || !m.content.length) return refuse("Unsupported reader turn");
      if (!pending) return refuse("A tool result with no tool call before it");
      const seen = new Set();
      const blocks = [];
      for (const b of m.content) {
        if (!isPlainObject(b) || b.type !== "tool_result") return refuse("Only tool results may be sent as blocks");
        if (Object.keys(b).some((k) => !["type", "tool_use_id", "content", "is_error"].includes(k))) {
          return refuse("Unsupported field in a tool result");
        }
        if (!pending.has(b.tool_use_id) || seen.has(b.tool_use_id)) return refuse("A tool result answers no open tool call");
        if (typeof b.content !== "string") return refuse("A tool result must be text");
        if (b.content.length > LIMITS.toolResult) return refuse("A tool result is too large", 413);
        seen.add(b.tool_use_id);
        const clean = { type: "tool_result", tool_use_id: b.tool_use_id, content: b.content };
        if (b.is_error === true) clean.is_error = true;
        blocks.push(clean);
      }
      if (seen.size !== pending.size) return refuse("Every tool call needs one result");
      pending = null;
      out.push({ role: "user", content: blocks });
    } else {
      const raw = typeof m.content === "string" ? [{ type: "text", text: m.content }] : m.content;
      if (!Array.isArray(raw) || !raw.length) return refuse("Unsupported assistant turn");
      const ids = new Set();
      const blocks = [];
      for (const b of raw) {
        if (!isPlainObject(b) || "cache_control" in b) return refuse("Unsupported assistant content");
        if (b.type === "text") {
          if (typeof b.text !== "string") return refuse("Unsupported assistant content");
          if (b.text.length > LIMITS.assistantText) return refuse("An earlier answer is too long", 413);
          if (b.text.trim()) blocks.push({ type: "text", text: b.text });
        } else if (b.type === "tool_use") {
          if (typeof b.id !== "string" || !TOOL_ID.test(b.id) || ids.has(b.id)) return refuse("Unsupported tool call");
          if (!TOOL_NAMES.has(b.name)) return refuse("Unknown tool");
          if (!isPlainObject(b.input) || JSON.stringify(b.input).length > LIMITS.toolInput) {
            return refuse("Unsupported tool call");
          }
          ids.add(b.id);
          blocks.push({ type: "tool_use", id: b.id, name: b.name, input: b.input });
        } else {
          return refuse("Unsupported assistant content");
        }
      }
      if (!blocks.length) return refuse("Empty assistant turn");
      pending = ids.size ? ids : null;
      out.push({ role: "assistant", content: blocks });
    }
  }
  // Alternation starting with the reader means an odd count ends with them.
  if (out.length % 2 === 0) return refuse("The last turn must be the reader's");
  return { ok: true, messages: out };
}

/**
 * Mark the end of the conversation for caching.
 *
 * Only the system block used to be cached, so every round trip paid full price
 * for everything said so far, tool results included, and a question cost
 * several times what it needed to. A breakpoint on the last block means the
 * next round trip, and the next question, read all of this from the cache.
 */
function withCacheBreakpoint(messages) {
  const out = messages.slice();
  const last = out[out.length - 1];
  const blocks = typeof last.content === "string"
    ? [{ type: "text", text: last.content }]
    : last.content.map((b) => ({ ...b }));
  blocks[blocks.length - 1].cache_control = { type: "ephemeral" };
  out[out.length - 1] = { role: last.role, content: blocks };
  return out;
}

/**
 * Whose allowance a request counts against.
 *
 * An IPv4 address as it is. An IPv6 address by its /64, because one household
 * or phone is normally given a whole /64 and can pick a fresh address in it at
 * will, so counting single IPv6 addresses would be no limit at all.
 */
function expandIPv6(ip) {
  let s = String(ip).split("%")[0].toLowerCase();
  // An IPv4 tail (::ffff:1.2.3.4) becomes two groups.
  const v4 = s.match(/(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (v4) {
    const n = v4.slice(1).map(Number);
    s = s.slice(0, v4.index) + ((n[0] << 8) | n[1]).toString(16) + ":" + ((n[2] << 8) | n[3]).toString(16);
  }
  const halves = s.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
  if (fill < 0) return null;
  const groups = head.concat(new Array(fill).fill("0"), tail);
  if (groups.length !== 8 || groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
  return groups.map((g) => g.padStart(4, "0"));
}

function clientKey(ip) {
  const s = String(ip || "").trim();
  if (!s) return "unknown";
  if (s.includes(":")) {
    const g = expandIPv6(s);
    return g ? g.slice(0, 4).join(":") + "::/64" : "unknown";
  }
  return s;
}

/**
 * The browser's id from the request body, or "-" for none. Only a plain run
 * of 16 to 64 letters, digits, - or _ is taken, so it cannot be used to
 * smuggle anything into the counter's keys.
 */
function browserKey(id) {
  return typeof id === "string" && /^[A-Za-z0-9_-]{16,64}$/.test(id) ? id : "-";
}

function utcDay(now) {
  return new Date(now === undefined ? Date.now() : now).toISOString().slice(0, 10);
}

/**
 * The daily counters, in a Durable Object.
 *
 * They used to live in KV, which is the wrong tool for a counter: it reads,
 * waits for the whole model call, then writes, so parallel requests from one
 * address all read the same count and all pass, and on the free plan its
 * 1,000 writes a day ran out at a few hundred questions, after which the cap
 * failed open for everyone until midnight.
 *
 * A Durable Object handles one request at a time and its storage calls cannot
 * interleave, so a count is checked and taken in one step, before the model is
 * called, and handed back if the call fails. It holds today's counts only:
 * the first request of a new day, or the alarm set just after midnight UTC,
 * clears the day before, so no address is kept beyond the next day (the
 * privacy page says 48 hours).
 *
 * Declared in wrangler.toml as ASK_COUNTER, class AskCounter, SQLite-backed,
 * which the free plan allows.
 */
export class AskCounter {
  constructor(state, env) {
    this.state = state;
    this.storage = state.storage;
    this.env = env;
  }

  async rollover(day) {
    const held = await this.storage.get("day");
    if (held !== day) {
      await this.storage.deleteAll();
      await this.storage.put("day", day);
    }
    if (!(await this.storage.getAlarm())) {
      const next = Date.parse(day + "T00:00:00Z") + 86400000 + 60000;
      await this.storage.setAlarm(next);
    }
  }

  async alarm() {
    await this.rollover(utcDay());
  }

  async fetch(request) {
    const { op, key, browser, question, caps } = await request.json();
    const day = utcDay();
    await this.rollover(day);
    // Three counts: this browser on this network (the ten), the network as a
    // whole (its ceiling) and the site. The browser's is keyed under its
    // network, so an id carried to another network starts afresh there and
    // an id cannot be used to spend another network's allowance.
    const ipKey = "ip:" + key;
    const brKey = "b:" + key + "|" + browserKey(browser);
    const net = (await this.storage.get(ipKey)) || { q: 0, r: 0 };
    const mine = (await this.storage.get(brKey)) || { q: 0, r: 0 };
    const site = (await this.storage.get("site")) || { q: 0, r: 0 };
    const q = question ? 1 : 0;
    const left = () => Math.max(0, Math.min(caps.questions - mine.q, caps.networkQuestions - net.q));

    if (op === "refund") {
      for (const c of [mine, net, site]) {
        c.q = Math.max(0, c.q - q);
        c.r = Math.max(0, c.r - 1);
      }
      await this.storage.put({ [ipKey]: net, [brKey]: mine, site });
      return Response.json({ ok: true });
    }

    // A question is refused once today's questions are used up. Round trips
    // within a question already asked are bounded by the request counts, so
    // the last question of the day can still be finished.
    let refused = null;
    if (question && mine.q >= caps.questions) refused = "questions";
    else if (question && net.q >= caps.networkQuestions) refused = "network";
    else if (mine.r >= caps.requests || net.r >= caps.networkRequests) refused = "requests";
    else if ((question && site.q >= caps.siteQuestions) || site.r >= caps.siteRequests) refused = "site";
    if (refused) return Response.json({ allowed: false, refused, remaining: left() });

    for (const c of [mine, net, site]) {
      c.q += q;
      c.r += 1;
    }
    await this.storage.put({ [ipKey]: net, [brKey]: mine, site });
    return Response.json({ allowed: true, remaining: left() });
  }
}

function capsFor(env) {
  const n = (v, d) => {
    const x = parseInt(v, 10);
    return x > 0 ? x : d;
  };
  return {
    questions: DAILY_CAP,
    requests: REQUEST_CAP,
    networkQuestions: NETWORK_CAP,
    networkRequests: NETWORK_REQUEST_CAP,
    siteQuestions: n(env.SITE_DAILY_QUESTIONS, SITE_QUESTION_CAP),
    siteRequests: n(env.SITE_DAILY_REQUESTS, SITE_REQUEST_CAP),
  };
}

async function counter(env, op, key, browser, question) {
  // One object for the whole site: the counts are small, and one object is
  // what makes the site-wide ceiling exact.
  const stub = env.ASK_COUNTER.get(env.ASK_COUNTER.idFromName("daily"));
  const res = await stub.fetch("https://ask-counter/", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ op, key, browser: browserKey(browser), question, caps: capsFor(env) }),
  });
  if (!res.ok) throw new Error(`counter answered ${res.status}`);
  return res.json();
}

const REFUSALS = {
  questions: `You have used today's ${DAILY_CAP} questions. They reset at midnight UTC, and everything else on the map still works.`,
  network: "Today's questions from this network have been used. They reset at midnight UTC, and everything else on the map still works.",
  requests: "Today's questions have been used. They reset at midnight UTC, and everything else on the map still works.",
  site: "Ask has answered as many questions as it can for today. Please try again tomorrow. The rest of the map still works.",
  burst: "That was a lot of questions in a short time. Please wait a minute and try again.",
};

const UNAVAILABLE = "The assistant is unavailable right now.";

export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST") return json({ error: "POST only" }, 405, cors);
    if (!cors["Access-Control-Allow-Origin"]) return json({ error: "Origin not allowed" }, 403, cors);
    // Fails closed. Without the API key, the counter or the burst limiter the
    // Worker answers nothing, rather than answering without a limit: an
    // unbound counter used to look exactly like a working one.
    if (!env.ANTHROPIC_API_KEY || !env.ASK_COUNTER || !env.ASK_BURST) {
      console.log("not configured: needs ANTHROPIC_API_KEY, ASK_COUNTER and ASK_BURST (see wrangler.toml)");
      return json({ error: "Worker is not configured" }, 503, cors);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "Body must be JSON" }, 400, cors);
    }

    const checked = validateMessages(body && body.messages);
    if (!checked.ok) return json({ error: checked.error }, checked.status, cors);
    const messages = checked.messages;

    const key = clientKey(request.headers.get("CF-Connecting-IP"));
    const browser = body && body.client;
    const isQuestion = isNewQuestion(messages);

    // A burst limit first: it costs no storage, and it stops one address
    // firing requests in parallel before any of them reach the counter.
    try {
      const burst = await env.ASK_BURST.limit({ key });
      if (!burst || !burst.success) return json({ error: REFUSALS.burst, scope: "burst" }, 429, cors);
    } catch (e) {
      console.log(`burst limiter failed, refusing: ${e && e.message}`);
      return json({ error: UNAVAILABLE }, 503, cors);
    }

    // Counted before the model is called, so parallel requests cannot all
    // pass on the same count. Handed back below if the call fails.
    let held;
    try {
      held = await counter(env, "take", key, browser, isQuestion);
    } catch (e) {
      console.log(`counter failed, refusing: ${e && e.message}`);
      return json({ error: UNAVAILABLE }, 503, cors);
    }
    if (!held.allowed) {
      return json({ error: REFUSALS[held.refused] || REFUSALS.requests, scope: held.refused }, 429, cors);
    }
    const giveBack = async () => {
      try {
        await counter(env, "refund", key, browser, isQuestion);
      } catch (e) {
        console.log(`refund failed: ${e && e.message}`);
      }
    };

    let upstream;
    try {
      upstream = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-api-key": env.ANTHROPIC_API_KEY,
          "anthropic-version": "2023-06-01",
          // If a safety classifier declines, the API retries on a model that
          // can answer (fallbacks below) instead of failing the question.
          "anthropic-beta": "server-side-fallback-2026-07-01",
        },
        body: JSON.stringify({
          // Fixed here, never taken from the request: the model, the output
          // cap, the instructions and the tools are the Worker's alone.
          model: MODEL,
          max_tokens: MAX_TOKENS,
          thinking: THINKING,
          output_config: { effort: EFFORT },
          fallbacks: "default",
          system: [{ type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
          tools: TOOLS,
          messages: withCacheBreakpoint(messages),
        }),
      });
    } catch (e) {
      console.log(`anthropic unreachable: ${e && e.message}`);
      await giveBack();
      return json({ error: UNAVAILABLE }, 502, cors);
    }

    if (!upstream.ok) {
      const detail = await upstream.text();
      console.log(`anthropic ${upstream.status}: ${detail.slice(0, 400)}`);
      // Anthropic does not bill a call that failed, and neither do we.
      await giveBack();
      // Do not pass the upstream body back: it can echo request content.
      return json({ error: UNAVAILABLE }, 502, cors);
    }

    const data = await upstream.json();
    // What each call cost, for `wrangler tail`: worker/README.md prices a
    // question from these.
    if (data && data.usage) {
      console.log(JSON.stringify({ q: isQuestion, model: data.model, stop: data.stop_reason, usage: data.usage }));
    }
    // Under an underscore so it cannot collide with a field the API adds later.
    data._limit = { cap: DAILY_CAP, remaining: held.remaining, enforced: true };
    return json(data, 200, cors);
  },
};
