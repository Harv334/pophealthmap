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

That has three consequences worth understanding. Every figure in an answer is
the same number the map is drawing. No dataset is ever uploaded. And the model
cannot invent a statistic, because it has none in its context: if a tool
returns nothing, there is nothing for it to report.

## Deploying

You need a Cloudflare account and an Anthropic API key.

```bash
cd worker
npm install -g wrangler          # if you do not have it
wrangler login

# 1. Create the rate-limit store and paste the printed id into wrangler.toml
wrangler kv namespace create RATE_LIMIT

# 2. Store the API key as a secret. This never enters the repo.
wrangler secret put ANTHROPIC_API_KEY

# 3. Ship it
wrangler deploy
```

Wrangler prints a URL like `https://pophealthmap-ai.<subdomain>.workers.dev`.
Put it in `data/map/assistant.js`:

```js
var ASSISTANT_ENDPOINT = "https://pophealthmap-ai.<subdomain>.workers.dev";
```

Commit that and the panel appears. While it is empty the panel does not render
at all, so the map works normally without any of this.

## Checking it works

```bash
curl -sS -X POST "$WORKER_URL" \
  -H 'content-type: application/json' \
  -H 'Origin: https://pophealthmap.uk' \
  -d '{"messages":[{"role":"user","content":"Which ward is most deprived?"}]}' | head -c 400
```

Expect JSON with `"stop_reason":"tool_use"` — the model asking for a tool,
which is the browser's job to run. A `403` means the Origin header is not in
`ALLOWED_ORIGINS`.

## What it costs, and how to cap it

`claude-haiku-4-5` at $1 per million input tokens and $5 per million output,
capped at 1024 output tokens, with the system prompt marked for caching.

One question is roughly two API calls: the model asks for a tool, the browser
runs it, the model answers. That works out at about **half a penny per
question**, so a hundred questions a day is around 50p.

Three limits sit in front of that, and only one of them is a real spend cap.

**1. The per-IP cap in this Worker.** `DAILY_CAP` in `src/index.js`, 40
questions per IP per UTC day. Held in KV, counted per question rather than per
round trip. It stops one person hammering it; it does not stop a thousand
people. Note it fails open: if KV errors, the request is answered and not
counted, because breaking the site is worse than losing the count.

**2. Cloudflare's free tier.** 100,000 Worker requests a day, and 1,000 KV
writes a day. The KV write limit is the binding one, at one write per question.
Past it the cap simply stops counting. Neither costs money; they just stop.

**3. The Anthropic spend limit.** This is the one that actually bounds the
bill, and it is the only one that does. **Set it before you deploy.**

Console → Settings → Billing → Spend limits → Set limit. Pick a number you
would not mind paying, such as $20 a month. At half a penny a question that is
about 4,000 questions. Usage pauses when it is reached; it does not overrun.

Better still, put this project in its own Workspace (Console → Workspaces),
create the API key inside it, and set the spend limit on the Workspace. Then
the cap applies to this project alone and cannot be spent by anything else you
build, and the key can be revoked without touching your other work.

Your organisation also has a tier spend cap above whatever you set, $500 a
month on the Start tier, but do not rely on that as your limit. It is a
backstop, not a budget.

## Security notes

- `ALLOWED_ORIGINS` is an exact-match allowlist. There is no wildcard, and an
  unlisted origin gets no CORS header.
- Upstream error bodies are logged, not returned, because they can echo request
  content back to the caller.
- Request size and conversation length are bounded; this endpoint is
  unauthenticated by design, so those limits are the backstop.
- The key exists only as a Cloudflare secret. It is not in this repo, not in
  `wrangler.toml`, and not in any frontend file.
