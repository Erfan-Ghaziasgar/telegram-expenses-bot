# Telegram Expenses Bot

Logs simple expenses/debts into Supabase Postgres and can return weekly/monthly summaries.

Runs as a FastAPI app using Telegram webhooks (suitable for Vercel serverless).

## Setup

1. Create a bot with `@BotFather` and copy the token.
2. Create a Supabase project and copy the Postgres connection string (`DATABASE_URL`).
   - For Vercel/serverless, prefer the Supabase "Transaction pooler" connection string (port `6543`).
3. Install dependencies:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r requirements.txt`
4. Create `.env` from the template and set:
   - `cp .env.example .env`
   - `TELEGRAM_BOT_TOKEN="123:abc..."`
   - `DATABASE_URL="postgresql://..."`
   - Optional: `TELEGRAM_ALLOWED_USER_IDS="123456789"` (comma-separated)
   - Optional (recommended): `TELEGRAM_WEBHOOK_SECRET_TOKEN="..."`
   - Optional: `LOG_LEVEL="INFO"`
   - Note: `.env` is in `.gitignore` (not committed)

Run locally:
- `uvicorn api.index:app --reload`

## Deploy (Vercel)

- This repo includes `vercel.json` and exposes the FastAPI app from `api/index.py`.
- After deploying, set the Telegram webhook to your Vercel URL:
  - Webhook URL: `https://<your-domain>/telegram/webhook`
  - If you set `TELEGRAM_WEBHOOK_SECRET_TOKEN`, also set the same `secret_token` on Telegram.

Example (set webhook):

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<your-domain>/telegram/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET_TOKEN>"
```

## Usage

To add a new record, run `/add` and follow the guided steps:
`type → person (if needed) → amount → description → confirm`.

Privacy note: the bot is intended to be used in a private chat only (it will refuse to run in groups).

Dates: summaries and timestamps show both Gregorian and Jalali (Shamsi) dates.

Commands:
- `/add` (start a new record - guided)
- `/id` (show your Telegram user id)
- `/menu` (show command buttons)
- `/hide` (hide command buttons)
- `/last [n]` (show recent records)
- `/undo` (delete last record)
- `/delete <id>` (delete by id)
- `/edit <id>` (edit by id - guided)
- `/week` (weekly summary)
- `/month` (monthly summary)
- `/help`
