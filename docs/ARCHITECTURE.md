# Architecture

## High-level

```
┌──────────────────┐   HTTPS    ┌─────────────────────────────────────────────┐
│  Next.js 15 SPA  │ ─────────▶ │  nginx                                      │
│  (App Router)    │   WSS      │   ├─ /api/*      → FastAPI                  │
│                  │            │   ├─ /ws/*       → FastAPI websocket        │
└──────────────────┘            │   └─ /*          → Next.js                  │
                                └─────────────────────────────────────────────┘
                                                  │
                  ┌───────────────────────────────┼───────────────────────────────┐
                  ▼                               ▼                               ▼
            ┌──────────────┐              ┌──────────────┐               ┌──────────────┐
            │ FastAPI app  │              │ Background   │               │ Websocket    │
            │ (uvicorn)    │              │ worker (arq) │               │ gateway      │
            └──────┬───────┘              └──────┬───────┘               └──────┬───────┘
                   │                             │                              │
        ┌──────────┼───────────┬─────────────────┼─────────────┐                │
        ▼          ▼           ▼                 ▼             ▼                ▼
   ┌─────────┐ ┌────────┐ ┌──────────┐    ┌──────────┐  ┌──────────┐     ┌──────────────┐
   │Postgres │ │ Redis  │ │ ChromaDB │    │ OpenAI   │  │ Market   │     │ Subscribed   │
   │  (OLTP) │ │(cache/ │ │ (vectors)│    │  API     │  │ data     │     │ clients      │
   │         │ │queues) │ │          │    │          │  │ APIs     │     │              │
   └─────────┘ └────────┘ └──────────┘    └──────────┘  └──────────┘     └──────────────┘
```

## Why this shape

- **Async FastAPI** — the workload is I/O-bound (LLM calls, vendor APIs, websocket fan-out), so a single async process with workers scales further than a threaded WSGI app.
- **Postgres for OLTP** — users, subscriptions, watchlists, alerts, reports. Strong consistency, relational queries, well understood ops.
- **Redis** — short-lived cache (price ticks, indicator snapshots), task queue for arq, rate-limit counters, refresh-token deny-list.
- **ChromaDB** — vector store for the methodology RAG corpus. Per-user partitioning via `metadata.user_id`.
- **arq workers** — background jobs (document ingestion, alert evaluation, backtests). Same Python process model as the API so models/services are reused.
- **nginx** — terminates TLS, single-port public surface, websocket pass-through.

## Multi-tenancy

Every model row that is user-owned carries a `user_id` foreign key. Every service function takes the current user and never reads cross-user data without an explicit admin scope. RAG retrieval is filtered by `metadata.user_id` in the Chroma query.

## Phase 1 — Structured Equity Model — request flow

```
POST /api/v1/equity/run     body: { ticker: "GOOGL" }
        │
        ▼
EquityAnalysisService.run(user, ticker)
   ├─ fundamentals = MarketDataService.fundamentals(ticker)    # live numbers
   ├─ corpus_chunks = RAGService.retrieve(user, "Structured Equity Master Instructions", k=8)
   ├─ system_prompt = build_master_instruction_block(corpus_chunks)
   ├─ raw_output    = OpenAIService.chat(system=system_prompt, user=f"Company: {ticker}\nFundamentals:\n{fundamentals}")
   ├─ parsed        = NineStepParser.parse(raw_output)
   ├─ scores        = parsed.extract_category_scores()
   ├─ composite     = mean(scores.values())
   ├─ conviction    = ConvictionBands.classify(composite)
   ├─ report        = EquityReport.persist(...)
   └─ return EquityReportResponse(...)
```

`NineStepParser` enforces the 9 sections in order and refuses to persist a partial report.

## Phase 2 — live signals (scaffolded)

```
MarketDataService.subscribe(symbol) ──▶ Redis pubsub ──▶ WebsocketGateway
                                              │
                                              ▼
                                      AlertEvaluator (worker)
                                              │
                                              ▼
                                 NotificationService (in-app / email / TG / Discord)
```

Indicator computations are vectorized pandas/numpy — each indicator is a pure function `(df: DataFrame) → Series`. Composition into setups is in `services/strategies/`.

## Security

- bcrypt for password hashes (passlib).
- JWT access (short) + refresh (long) tokens; refresh rotates on use; reuse of an already-rotated refresh revokes the whole family (token-family pattern).
- Refresh tokens stored hashed in Postgres with `revoked_at`.
- Per-user rate limits in Redis.
- Strict CORS allowlist via env.
- All write endpoints require `Depends(current_user)`; admin endpoints require `current_user.role == "admin"`.

## Observability

- Structured JSON logs via `structlog` with request-id middleware.
- `/health` and `/health/deep` endpoints.
- Prometheus metrics under `/metrics` (request count, latency, OpenAI tokens, LLM cost).
