# Free (or near-free) setup

The only thing in the standard setup that costs real money is the LLM. Below are four ways to run the equity model without paying OpenAI directly, ordered from easiest to most setup. Pick one, copy its preset block from `.env.example` into `.env`, and you're good.

## TL;DR — what to pick

| Goal | Pick | Cost | Quality vs GPT-4o |
|------|------|------|-------------------|
| **Just want it free, fast, easy** | Groq | $0 (free tier, ~100 req/day) | ~85% |
| **No internet / fully private** | Ollama (local) | $0 (uses your Mac) | ~70% |
| **Use Claude specifically** | OpenRouter → Claude | ~$0.01–0.03 / report after free trial | ~98% |
| **Best quality, cheapest paid** | DeepSeek | ~$0.001 / report | ~95% |

Each free OpenAI account also gets ~$5 of free credit on signup that lasts a few months — fine for testing if you don't mind paying eventually.

---

## Option 1 — Groq (recommended for most people)

Free tier hosts Llama 3.3 70B at very high speed. ~100 requests/day free, no card on file required.

**Setup:**

1. Sign up at [console.groq.com](https://console.groq.com).
2. API Keys → Create API key. Copy the `gsk_...` value.
3. Open `.env` and replace the `OPENAI_*` lines with:
   ```
   OPENAI_API_KEY=gsk_your_key_here
   OPENAI_BASE_URL=https://api.groq.com/openai/v1
   OPENAI_MODEL_CHAT=llama-3.3-70b-versatile
   OPENAI_MODEL_EMBED=text-embedding-3-small
   LLM_DISABLE_JSON_MODE=false
   ```
4. `docker compose up --build`

**What about embeddings?** Groq doesn't host embedding models. The app detects this and falls back to a deterministic hash-vector embedding for your uploaded methodology. RAG retrieval quality is approximate but the 9-step model still runs fine because the Master Instruction Block is sent verbatim to the LLM regardless.

If you want real embeddings + Groq chat, run Ollama alongside (Option 2) just for embeddings, or get a free OpenAI key for embeddings only — embeddings are cheap (~$0.0001 per CC PDF).

---

## Option 2 — Ollama (fully local, no internet, no quotas)

Runs the model on your Mac. Apple Silicon Macs (M1+) handle Llama 3.1 8B comfortably. Intel Macs will struggle.

**Setup:**

1. Install Ollama from [ollama.com](https://ollama.com). It's a normal Mac app.
2. Open Terminal:
   ```
   ollama pull llama3.1:8b
   ollama pull nomic-embed-text
   ```
   (~5 GB download)
3. Open `.env` and use:
   ```
   OPENAI_API_KEY=ollama
   OPENAI_BASE_URL=http://host.docker.internal:11434/v1
   OPENAI_MODEL_CHAT=llama3.1:8b
   OPENAI_MODEL_EMBED=nomic-embed-text
   LLM_DISABLE_JSON_MODE=true
   ```
4. Make sure Ollama is running (it shows a llama in your menu bar).
5. `docker compose up --build`

**Quality note:** Llama 3.1 8B is noticeably weaker than GPT-4o or Llama 3.3 70B on the 9-step task. It will produce reports, but expect occasional structural slips (missing sections, weaker financial reasoning). For better quality on Apple Silicon, swap to `qwen2.5:14b` or `qwen2.5:32b` if you have the RAM (32 GB+ recommended for 32b).

**Reports take longer locally** — 1–3 minutes vs ~20 seconds with Groq/OpenAI.

---

## Option 3 — OpenRouter (the way to use Claude in this app)

One account, all the popular models, including Claude 3.5 Sonnet, Gemini, GPT-4o, and a **free Llama 3.3 70B route**.

**Setup:**

1. Sign up at [openrouter.ai](https://openrouter.ai). New accounts get a small free trial credit.
2. Keys → Create API key. Copy the `sk-or-v1-...` value.
3. Open `.env`:
   ```
   OPENAI_API_KEY=sk-or-v1-your_key
   OPENAI_BASE_URL=https://openrouter.ai/api/v1
   OPENAI_MODEL_CHAT=meta-llama/llama-3.3-70b-instruct:free
   OPENAI_MODEL_EMBED=text-embedding-3-small
   LLM_DISABLE_JSON_MODE=false
   ```
4. To switch models without changing keys, just change `OPENAI_MODEL_CHAT`:
   - `anthropic/claude-3.5-sonnet` — Claude (premium)
   - `google/gemini-2.0-flash-exp:free` — free Gemini
   - `openai/gpt-4o-mini` — cheap OpenAI
   - `deepseek/deepseek-chat` — cheap, near-Claude quality
5. `docker compose up --build`

OpenRouter is the simplest way to A/B different models against your methodology and decide what's worth paying for.

---

## Option 4 — Anthropic API direct (Claude only)

If you already have an Anthropic API account or want to use only Claude, you can use the Anthropic API directly. The new-account $5 free credit gets you roughly 25–50 equity reports.

**Important note about billing:** Your Claude.ai subscription (the chat interface) and the Anthropic API are billed separately. The API has its own credits at [console.anthropic.com](https://console.anthropic.com). Sign-in is the same account, but you need to add API credits separately.

Anthropic's API uses a slightly different message shape than OpenAI's, so the current code path doesn't drop straight in. **Two options:**

- **(Easiest) Go through OpenRouter** — see Option 3. OpenRouter exposes Claude through an OpenAI-compatible interface, so the existing code works unchanged.
- **(Native) Add Anthropic SDK** — small patch: add an `anthropic` provider in `app/services/equity/llm.py`. Tell me if you want me to wire this and I'll do it.

---

## Option 5 — DeepSeek (cheapest paid option, near-Claude quality)

Not free, but ~50× cheaper than OpenAI. ~$0.001 per equity report.

```
OPENAI_API_KEY=sk-your_deepseek_key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL_CHAT=deepseek-chat
OPENAI_MODEL_EMBED=text-embedding-3-small
LLM_DISABLE_JSON_MODE=false
```

Sign up at [platform.deepseek.com](https://platform.deepseek.com). Embeddings still need OpenAI or hash fallback.

---

## Cost calibration

Typical 9-step equity report uses ~5,000 input tokens (the Master Instruction Block + RAG context + fundamentals) and ~3,000 output tokens (the JSON report). Per-report cost across providers:

| Provider | Cost per report |
|----------|-----------------|
| Ollama (local) | $0 |
| Groq free tier | $0 (within quota) |
| OpenRouter Llama 3.3 70B free | $0 |
| DeepSeek | ~$0.001 |
| OpenRouter → Gemini Flash | ~$0.003 |
| GPT-4o-mini | ~$0.005 |
| Claude 3.5 Sonnet | ~$0.024 |
| GPT-4o | ~$0.045 |

For 10 reports/day, that's $0 → $13.50/month depending on provider.

---

## Switching providers later

You can change providers any time by editing `.env` and restarting:

```
docker compose down
# edit .env
docker compose up
```

No data is lost — your uploaded methodology, watchlists, alerts, and prior reports stay in Postgres.
