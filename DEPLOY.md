# Deploying CC Trader so friends can use it

Three options, ordered from fastest to most permanent.

---

## Option A — ngrok tunnel (5 minutes, your Mac stays the server)

Best for: showing it to a few friends quickly. URL is ephemeral and your Mac must stay on with the server running.

### Steps

1. **Install ngrok**:
   ```
   brew install ngrok
   ```
   (If you don't have Homebrew, download from https://ngrok.com/download)

2. **Sign up for a free ngrok account** at https://ngrok.com → grab your auth token from the dashboard.

3. **Authenticate**:
   ```
   ngrok config add-authtoken YOUR_TOKEN_HERE
   ```

4. **Start the CC Trader server** in one terminal:
   ```
   cd ~/Documents/Claude/Projects/Stocks/cc-trader
   python3 scan_setups.py --serve
   ```
   Wait for `✓ Scan complete`.

5. **In a SECOND terminal**, start the tunnel:
   ```
   ngrok http 8080
   ```

6. ngrok prints a public URL like:
   ```
   Forwarding  https://abc1-23-45-67-89.ngrok-free.app -> http://localhost:8080
   ```
   Share that `https://...` URL with your friends. It works on phones too.

**Stop**: Ctrl+C in both terminals.

**Limitations**:
- URL changes every time you restart ngrok (free tier).
- Your Mac must be on with the server running.
- Free tier shows an "ngrok warning page" on first visit — clicking through is fine.

---

## Option B — Render.com (15 minutes, permanent free URL)

Best for: sharing a URL that works 24/7 even when your Mac is off. Free tier sleeps after 15 min of no traffic (cold start ~10-20 seconds on the next visit).

### Steps

1. **Push code to GitHub**:

   In your terminal:
   ```
   cd ~/Documents/Claude/Projects/Stocks/cc-trader

   # Create a fresh git repo
   git init
   git add scan_setups.py requirements_standalone.txt render.yaml Procfile DEPLOY.md
   git commit -m "CC Trader — standalone scanner"

   # Make sure .env is NEVER committed (your Groq key is there)
   echo ".env" > .gitignore
   echo ".env" >> .gitignore
   git add .gitignore
   git commit -m "gitignore"
   ```

   Then create an empty repo on https://github.com/new, name it `cc-trader-standalone`, and push:
   ```
   git remote add origin https://github.com/YOUR_USERNAME/cc-trader-standalone.git
   git branch -M main
   git push -u origin main
   ```

2. **Create Render account** at https://render.com (free, sign in with GitHub).

3. **Create the service**:
   - Dashboard → **New +** → **Web Service**
   - Connect your GitHub → pick `cc-trader-standalone` repo
   - Render auto-detects `render.yaml`. If not, fill in manually:
     - **Name**: `cc-trader`
     - **Build Command**: `pip install -r requirements_standalone.txt`
     - **Start Command**: `python scan_setups.py --serve --port $PORT --refresh 60 --cache 600`
     - **Plan**: Free

4. **Add your Groq API key**:
   - In the service page → **Environment** tab
   - Add Environment Variable:
     - Key: `OPENAI_API_KEY`
     - Value: `gsk_YOUR_KEY_FROM_GROQ_CONSOLE`
   - Save

5. Click **Deploy**.

6. After ~3-5 minutes you get a URL like:
   ```
   https://cc-trader-XXX.onrender.com
   ```
   That's your permanent URL. Share it.

**Tips**:
- First visit after 15 min of idle = 10-20 second cold start. After that it's snappy.
- To stop sleeping: upgrade to paid tier ($7/mo).
- Logs: Render dashboard → service → **Logs** tab.
- Re-deploy: push to GitHub `main`, Render auto-deploys.

---

## Option C — Fly.io (15 minutes, always-on free tier)

Best for: free + never sleeps. Slightly more CLI work.

### Steps

1. **Install flyctl**:
   ```
   brew install flyctl
   ```

2. **Sign up + login**:
   ```
   fly auth signup
   # follow the prompts
   ```

3. **Launch from project directory**:
   ```
   cd ~/Documents/Claude/Projects/Stocks/cc-trader
   fly launch --no-deploy
   ```
   - When asked, say YES to "use existing fly.toml"
   - When asked about Postgres/Redis, say NO to both.

4. **Set your Groq key as a secret**:
   ```
   fly secrets set OPENAI_API_KEY=gsk_YOUR_KEY_FROM_GROQ_CONSOLE
   ```

5. **Deploy**:
   ```
   fly deploy
   ```

6. After ~5 minutes:
   ```
   fly open
   ```
   Opens your URL like `https://cc-trader.fly.dev`.

**Logs**: `fly logs -a cc-trader`

---

## Security considerations when going public

The standalone script has no authentication. Anyone with the URL can:
- Trigger ad-hoc scans (uses your Groq quota — 1000 req/day free tier)
- See whatever your watchlist contains

For "show some friends" this is fine. To prevent abuse:

**Option 1 — HTTP Basic Auth** (quick, ~5 min code change):
Add a simple `Authorization: Basic` check in the server's request handler. Each friend uses the same shared password.

**Option 2 — Rate limit**:
Add per-IP rate limiting (e.g. max 10 requests/min) in the handler.

**Option 3 — Just monitor**:
Check your Groq dashboard occasionally for usage. If quota gets blown, rotate the key.

I recommend Option 3 for now — you'll see in the Groq dashboard if usage spikes, and you can rotate the key any time.

---

## Mobile experience

The current HTML is laid out for desktop (1100px+ width). On phones:
- The summary table will be horizontally scrollable.
- The TradingView widgets are responsive — they auto-fit.
- The setup cards stack below the chart on narrow screens.

It's usable on phone but not optimized. Let me know if you want me to add mobile-specific layout.

---

## Quick test before sharing

After deploying, before sharing the URL with friends:

1. Open the URL on your phone (over cellular, not WiFi) — verify it loads.
2. Type a ticker in the search box → confirm a scan runs.
3. Click a star/bell — verify they work on mobile too.
4. Check that the chart renders.

If anything's broken, fix locally first, then push/deploy again.
