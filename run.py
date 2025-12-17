"""
Telegram bot for logging expenses/debts.

Env vars:
- TELEGRAM_BOT_TOKEN (or BOT_TOKEN): required
- TELEGRAM_ALLOWED_USER_IDS: optional, comma-separated user ids
- DATABASE_URL: required (Supabase Postgres connection string)
- TELEGRAM_WEBHOOK_SECRET_TOKEN: optional (recommended)
- LOG_LEVEL: optional (default: INFO)

Run:
  python3 run.py
  # with auto-reload:
  UVICORN_RELOAD=1 python3 run.py
  # or:
  uvicorn api.index:app --reload
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    reload = (os.environ.get("UVICORN_RELOAD") or "").strip() in {"1", "true", "yes", "on"}
    uvicorn.run("api.index:app", host="0.0.0.0", port=port, reload=reload)


if __name__ == "__main__":
    main()
