# Quick start

## Prerequisites

- Docker + Docker Compose
- (Required) `OPENAI_API_KEY` for the equity model
- (Optional) `POLYGON_API_KEY`, `ALPACA_*`, `STRIPE_*` for Phase 2/3 features

## 1. Configure

```bash
cd cc-trader
cp .env.example .env
# Edit .env — at minimum set:
#   SECRET_KEY=...  (any long random string, e.g. `openssl rand -hex 32`)
#   OPENAI_API_KEY=sk-...
```

## 2. Boot the stack

```bash
docker compose up --build
```

Compose starts Postgres, Redis, ChromaDB, the FastAPI backend, the arq worker, the Next.js frontend, and nginx.

First boot:
- Backend runs `alembic upgrade head` automatically.
- API: <http://localhost:8000/docs>
- Frontend: <http://localhost:3000>
- All-in-one (via nginx): <http://localhost>

## 3. Seed the public watchlist (optional)

```bash
docker compose exec backend python -m app.services.equity.seed
```

This creates the "Chart Champions 2026 Research" public watchlist (49 tickers across 13 sectors).

## 4. Create your account

1. Open <http://localhost:3000/register>.
2. Register with email + password (min 8 chars).
3. You're automatically logged in.

## 5. Upload your methodology

1. Click **Methodology** in the sidebar.
2. Upload `Structured Equity Model Master Instructions.pdf` and the CC Cheatsheet.
3. Wait for status to flip to **ready** (ingestion takes ~10–30s for typical CC PDFs; cheatsheets that are screenshot-heavy will OCR via tesseract and take longer).

## 6. Run your first analysis

1. Navigate to **Equity Model**, enter `GOOGL`, click **Open**.
2. Click **Run analysis**.
3. Wait 20–60 seconds for the 9-step report.

You will see:
- Step-by-step narrative analysis (Steps 1–9 in strict order).
- Seven category scores with bars (Business Quality, Financial Quality, ...).
- Composite score + conviction band.
- Bull/Base/Bear price scenarios.
- Invalidation triggers.
- Methodology citations to the chunks retrieved from your uploaded PDFs.

## Verification — run the deterministic unit tests

```bash
cd backend
pip install -r requirements.txt
SECRET_KEY=test \
  DATABASE_URL=sqlite+aiosqlite:///:memory: \
  DATABASE_URL_SYNC=sqlite:///:memory: \
  REDIS_URL=redis://localhost:6379/0 \
  PYTHONPATH=. \
  pytest tests/test_scoring.py tests/test_parser.py tests/test_indicators.py -q
```

All 22 tests should pass. These cover:
- The composite-rating formula (arithmetic mean, no weighting).
- The 5 conviction bands at their exact thresholds.
- The 9-step JSON parser (including fenced-JSON and missing-section rejection).
- Vectorised indicators (RSI bounds, MACD components, Bollinger ordering, Fibonacci, CC region, swing pivots, S/R clustering).

## Making yourself admin

After registration, promote your user to admin from the DB:

```bash
docker compose exec postgres psql -U cctrader -d cctrader \
  -c "UPDATE users SET role='admin' WHERE email='you@example.com';"
```

Then reload — the **Admin** link appears in the sidebar.

## Phase status

| Phase | Status | What works today |
|-------|--------|------------------|
| 1 — Structured Equity Model | **deeply implemented** | RAG ingestion · 9-step run · scoring · conviction · persistence · dashboard |
| 2 — Live technical signals  | **scaffolded**       | market-data adapters (yfinance, Polygon, Binance) · indicators · alert engine · websocket pump · alert worker cron |
| 3 — Backtest + paper trade  | **scaffolded**       | vectorised backtester · two seed strategies (EMA 55/100/200, RSI mean-rev) · paper portfolio + open/close trades |
| 4 — Billing + admin         | **scaffolded**       | Stripe checkout + portal · webhook handler · admin user list / disable / system health |

## Honest limits

- **OpenAI key required.** Without it, `/equity/run` returns 503.
- **Polygon/Alpaca optional.** Without keys, all market data falls back to yfinance (rate-limited, no intraday for free).
- **OCR is a hint, not perfection.** The Chart Champions cheatsheet PDFs are iPhone screenshot exports; tesseract handles them but not all text will round-trip cleanly. The Master Instruction Block is text-native and ingests perfectly.
- **The technical-pattern detectors (Three Drives, harmonics, Wickoff phases) are designed but not yet implemented.** The methodology is captured in `docs/METHODOLOGY.md`; Phase 2 work plan is to operationalise them one at a time. See section B of that doc.
- **Backtest engine is a baseline.** Two strategies ship; the operator-supplied cheatsheets contain many more candidates to add.
