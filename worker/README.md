# PopHealth Map AI Worker

A Cloudflare Worker that sits between the map and the Anthropic API.

Its only job is to hold the API key. The browser cannot be trusted with one, so
the page talks to this and this talks to Anthropic.

## How the answers stay honest

The model is never given the data. It is given four tool definitions, and the
**browser** runs them against the JSON the map has already loaded
(`data/map/assistant.js`). The flow is:

1. Browser sends the conversation here, and this forwards it with the tools.
2. The model replies with `stop_reason: "tool_use"`.
3. The browser executes the tools locally and sends the results back.
4. The model writes the answer around numbers it was handed.

That has two consequences worth understanding. No dataset is ever uploaded,
and the figures the model is handed are the same numbers the map is drawing.
It does not guarantee that every number in an answer is one of them: the only
safeguard is the system prompt's instruction to use the looked-up figures, and
answers can still contain derived numbers (differences, ratios, rounding) or
mistakes, which is why the panel asks readers to check any figure.

## Deploying

You need a Cloudflare account and an Anthropic API key.

The Worker is deployed as **`pophealthmapai`**. Keep the `name` in
`wrangler.toml` matching it: wrangler deploys to whatever that says, so a
mismatch quietly creates a second Worker at a second URL, with no bindings and
no secret, while the site carries on calling the first.

### With wrangler (version 4.36 or later)

```bash
cd worker
npm install -g wrangler@latest   # the burst limit needs 4.36 or later
wrangler login

# 1. Store the API key as a secret. This never enters the repo.
wrangler secret put ANTHROPIC_API_KEY

# 2. Ship it. This also creates the daily counter (a Durable Object, from the
#    [[migrations]] entry) and the burst limit ([[ratelimits]]); there is
#    nothing to create by hand.
wrangler deploy
```

The counter used to be a KV namespace (`RATE_LIMIT`). It is no longer used,
and once this version is live it can be deleted under Storage & Databases → KV.

### Without Node installed

Durable Object classes and their migrations can only be created by a
`wrangler deploy` (or the Workers CI build connected to this repository), not
by pasting code into the dashboard editor. Pasting `src/index.js` alone gives a
Worker with no counter, which refuses every request with
`503 Worker is not configured`. Use wrangler once from any machine with Node;
after that, code-only changes can be pasted in the dashboard as before.

The secret `ANTHROPIC_API_KEY` and the plaintext variable `ALLOWED_ORIGINS`
(`https://pophealth.uk,https://www.pophealth.uk,https://harv334.github.io`) are
under **Workers & Pages → pophealthmapai → Settings → Variables and Secrets**.

Wrangler prints a URL like `https://pophealthmapai.<subdomain>.workers.dev`.
Put it in `data/map/assistant.js`:

```js
var ASSISTANT_ENDPOINT = "https://pophealthmapai.<subdomain>.workers.dev";
```

Commit that and the panel appears. While it is empty the panel does not render
at all, so the map works normally without any of this.

## Checking it works

```bash
curl -sS -X POST "$WORKER_URL" \
  -H 'content-type: application/json' \
  -H 'Origin: https://pophealth.uk' \
  -d '{"messages":[{"role":"user","content":"Which ward is most deprived?"}]}' | head -c 400
```

Expect JSON with `"stop_reason":"tool_use"`, the model asking for a tool,
which is the browser's job to run. A `403` means the Origin header is not in
`ALLOWED_ORIGINS`, and a `503` with `Worker is not configured` means the API
key, the counter or the burst limit is missing: the Worker **fails closed** and
answers nothing until all three are there.

The reply carries the reader's count:

```json
"_limit": { "cap": 10, "remaining": 9, "enforced": true }
```

The checks that do not reach Anthropic cost nothing and can be run freely: `GET`
returns 405, an unlisted `Origin` returns 403 with no CORS header, a body
without `messages` returns 400, and an image block returns 400. Note that
Cloudflare's edge may reject a plain scripted client with error 1010 before the
Worker ever runs; send a normal browser `User-Agent` or you will be testing
Cloudflare rather than this code.

The tests in `tests/pipeline/test_worker_*.py` run this code under Node with
fake bindings and a stubbed API, so they cost nothing:
`python3 tests/pipeline/test_worker_cap.py` (the caps),
`test_worker_followup.py` (which messages count as questions) and
`test_worker_guard.py` (what is refused, and what is sent upstream).

## What it costs, and how to cap it

`claude-sonnet-5` at $2 per million input tokens and $10 per million output.
Cached input costs $2.50 per million to write and $0.20 per million to read.
Answers are capped at 2,048 output tokens with thinking switched off.

**The old figure here was wrong.** It said a call sent about 1,500 input tokens
and a question cost half a penny. Measured requests were about 45,000
characters for a first question and 138,000 to 184,000 characters per round
trip later in a session, roughly $0.05 to $0.30 a question, because the 41 KB
`list_indicators` result was resent, uncached, with every later call.

What changed, and what a question should cost now:

- `list_indicators` sends a short key list (about 13 KB rather than 41 KB).
- The Worker marks the end of the conversation for caching as well as the
  system prompt, so each round trip and each later question reads what came
  before from the cache at a tenth of the price, instead of paying for it again.
- The panel keeps the conversation under 36 KB between questions, replacing
  older figures with a stub and then dropping the oldest questions, and the
  Worker refuses anything over 64 KB.

The fixed part of every call (instructions and tool definitions) is about
7,200 characters, roughly 2,100 tokens. A typical question is two or three
round trips: a lookup of about 4 KB (one ward or LA profile), up to 13 KB if
the key list is fetched, and an answer of a few hundred tokens. Estimated from
those sizes:

| | per question | per 100 questions |
|---|---|---|
| typical, cache warm (a session in progress) | $0.01 to $0.03 | $1 to $3 |
| first question, or after five idle minutes (cache written again) | $0.02 to $0.05 | $2 to $5 |
| worst case, nothing read from the cache | up to about $0.15 | up to about $15 |

**These are estimates from request sizes, not billed figures.** The Worker
logs every call's `usage` (input, output, cache write and cache read tokens).
Before relying on the table, run `wrangler tail`, ask ten typical questions,
and price them from those numbers; correct this table if they differ.

It ran on `claude-haiku-4-5` first. Haiku was changed because it kept getting
the deprivation scale wrong: it inverted IMD scores on its first live question,
and once corrected described a mean decile of 3 as the most deprived tenth.
Both are the kind of error that reads as authoritative and gets quoted. If cost
ever matters more than that, the model is one line in `src/index.js`, but test
the deprivation questions before trusting it, and check the caching minimum for
the new model (1,024 tokens on Sonnet 5; the fixed part here is about 2,100).

Four limits sit in front of that. The last is the only one that bounds the
bill outright.

**1. A burst limit per address.** 12 requests a minute from one IPv4 address
or one IPv6 /64, using Cloudflare's Workers Rate Limiting (`ASK_BURST`), which
costs no storage writes. It stops a script firing requests in parallel.

**2. The daily cap per address.** `DAILY_CAP` in `src/index.js`, **10**
questions per UTC day, and the panel promises that number in its opening
message, so the two have to move together. A second counter bounds round trips
(60 a day), so a forged continuation cannot slip past it. Counted in a Durable
Object (`ASK_COUNTER`) **before** the model is called, so parallel requests
cannot all pass on one count, and handed back if the call fails. Being per
address, everyone behind one NAT (an NHS trust, a school) shares one allowance.

**3. A daily ceiling for the whole site.** `SITE_DAILY_QUESTIONS` (500) and
`SITE_DAILY_REQUESTS` (2,000) in `wrangler.toml`. When either is reached, Ask
stops for everyone until midnight UTC and says so; the rest of the map is
unaffected. Set it from your budget: at the typical $0.01 to $0.03 a question,
500 questions is $5 to $15 a day, and at the worst case it could be $75. For a
$20 a month spend limit, a ceiling of about 30 questions a day (and 120
requests) keeps a busy day from spending the month.

All three **fail closed**. The KV counter they replace failed open: if KV
errored, or ran out of its 1,000 free writes a day (a few hundred questions),
or was not bound at all, questions were answered and not counted, and nothing
outside showed it.

**4. The Anthropic spend limit.** This is the one that actually bounds the
bill. **Set it before you deploy**, to a figure that matches the site ceiling
above: Console → Settings → Billing → Spend limits → Set limit. Usage pauses
when it is reached; it does not overrun. When it trips, Ask stops for every
reader until it is raised or the month ends, which is why the site ceiling
should trip first.

Better still, put this project in its own Workspace (Console → Workspaces),
create the API key inside it, and set the spend limit on the Workspace. Then
the cap applies to this project alone and cannot be spent by anything else you
build, and the key can be revoked without touching your other work.

Your organisation also has a tier spend cap above whatever you set, $500 a
month on the Start tier, but do not rely on that as your limit. It is a
backstop, not a budget.

## Security notes

- `ALLOWED_ORIGINS` is an exact-match allowlist. There is no wildcard, and an
  unlisted origin gets no CORS header. A script can set any Origin header it
  likes, so this keeps other websites out, not scripts; the limits above and
  the payload checks below are what bound a script.
- Only the map's own conversation is forwarded. Turns must alternate; a typed
  question is plain text of at most 1,000 characters; the model's turns may
  hold only text and calls to the four tools; the reader's other turns only
  `tool_result` blocks answering the previous turn's calls, each at most 16 KB;
  and the whole conversation is at most 64 KB. Images, documents,
  `cache_control` and anything else are refused, and the messages sent upstream
  are rebuilt from the checked fields.
- The model, the output cap, the system prompt and the tools are fixed in the
  Worker. Anything the caller sends for them is ignored.
- Upstream error bodies are logged, not returned, because they can echo request
  content back to the caller.
- The key exists only as a Cloudflare secret. It is not in this repo, not in
  `wrangler.toml`, and not in any frontend file.
- The counter holds today's counts only, keyed by IPv4 address or IPv6 /64,
  and clears them at the first request of the next UTC day or by an alarm just
  after midnight, well inside the 48 hours the privacy page states.
