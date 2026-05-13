# Railway deployment

Railway expects one service per process. Create the following services in your project:

1. **postgres** — Railway-managed Postgres plugin.
2. **redis** — Railway-managed Redis plugin.
3. **chromadb** — deploy from `chromadb/chroma` Docker image, expose port 8000, mount a persistent volume at `/chroma/chroma`.
4. **backend** — point at `./backend`, root directory `cc-trader/backend`. Uses `infra/railway/railway.json`.
5. **worker** — point at `./backend`. Override start command:
   ```
   arq app.workers.main.WorkerSettings
   ```
6. **frontend** — point at `./frontend`. Railway autodetects Next.js; ensure `NEXT_PUBLIC_API_URL` env is set to the backend service's public URL.

## Required environment variables

Copy from `.env.example` and set in each service:

- `SECRET_KEY` (32+ bytes, random)
- `DATABASE_URL`, `DATABASE_URL_SYNC` (set from Postgres plugin)
- `REDIS_URL` (set from Redis plugin)
- `CHROMA_HOST`, `CHROMA_PORT`
- `OPENAI_API_KEY`
- `POLYGON_API_KEY`, `ALPACA_*`, `BINANCE_*` (optional)
- `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_ENTERPRISE`
- `SMTP_*` for password reset / verification emails
- `ALLOWED_ORIGINS` — comma-separated list including your frontend URL
- `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`

## After first deploy

1. Open the backend's web shell and run any pending migrations: `alembic upgrade head`.
2. POST `/api/v1/auth/register` to create your admin user, then bump that user's `role` to `admin` directly in the DB.
3. Visit `/documents` and upload your Chart Champions PDFs.
4. Visit `/equity/GOOGL` to verify the model runs end-to-end.
