# CC Trader — AI-Powered Trading Intelligence SaaS

A production-grade, multi-user SaaS that implements the **Chart Champions Structured Equity Analysis Model** as Phase 1, with infrastructure scaffolding for Phase 2 (live technical signals, alerts, backtesting, paper trading).

The trading methodology is sourced **exclusively** from the Chart Champions documents uploaded by the operator. The AI never invents trading logic — it executes the uploaded methodology over a RAG pipeline.

---

## Repository layout

```
cc-trader/
├── backend/                 FastAPI async backend
│   ├── app/
│   │   ├── api/v1/          REST routers
│   │   ├── core/            config, security, db, redis, logging
│   │   ├── db/              base SQLAlchemy + session
│   │   ├── models/          ORM models (one per domain entity)
│   │   ├── schemas/         Pydantic request/response schemas
│   │   ├── services/        business logic
│   │   │   ├── equity/      9-step Structured Equity Model
│   │   │   ├── rag/         document chunking, embeddings, retrieval
│   │   │   ├── market/      provider-agnostic market data
│   │   │   ├── indicators/  RSI/MACD/EMA/SMA/VWAP/ATR/BB/Fib/S-R
│   │   │   ├── alerts/      alert definitions + evaluator
│   │   │   ├── billing/     Stripe
│   │   │   ├── paper_trading/
│   │   │   ├── backtesting/
│   │   │   └── admin/
│   │   ├── workers/         background jobs (asyncio + arq/RQ)
│   │   ├── websocket/       streaming gateway
│   │   └── main.py          ASGI entrypoint
│   ├── migrations/          Alembic
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                Next.js 15 (App Router) + TS + Tailwind + shadcn
│   ├── app/                 routes
│   ├── components/          UI primitives + feature components
│   ├── lib/                 API client, hooks
│   └── Dockerfile
├── infra/
│   ├── nginx/
│   ├── railway/
│   └── github-actions/
├── docs/
│   ├── ARCHITECTURE.md
│   └── METHODOLOGY.md       extracted Chart Champions rules
├── docker-compose.yml
└── .env.example
```

## Quick start (local)

```bash
cp .env.example .env          # fill in OPENAI_API_KEY, etc.
docker compose up --build
# backend  → http://localhost:8000/docs
# frontend → http://localhost:3000
```

On first boot:
1. Register a user at `/register`.
2. Upload your Chart Champions PDFs at `/documents` (the Structured Equity Model Master Instructions + cheatsheets).
3. Visit `/equity/GOOGL` to run the 9-step analysis.

## Phase status

| Phase | Status | What it covers |
|-------|--------|----------------|
| 1 — Structured Equity Model | **deeply implemented** | RAG ingestion, 9-step run, scoring, conviction, persistence, dashboard |
| 2 — Live technical signals  | **scaffolded** | market data adapters, indicators, alert engine, websocket — wiring complete, broker keys required to activate |
| 3 — Backtest + paper trade | **scaffolded** | models + service skeletons with vectorized indicator engine |
| 4 — Billing + admin         | **scaffolded** | Stripe webhooks, subscription tiers, admin endpoints |

## Honest scope note

A full TradingView-class SaaS is months of work for a team. This repo is a coherent foundation: Phase 1 is the working vertical slice you can demo today; Phases 2–4 have all schemas, services, routes, and infrastructure in place — they need API keys, integration testing, and operational hardening before production.

See `docs/ARCHITECTURE.md` and `docs/METHODOLOGY.md`.
