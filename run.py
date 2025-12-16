"""
Telegram bot for logging expenses/debts.

Env vars:
- TELEGRAM_BOT_TOKEN (or BOT_TOKEN): required
- TELEGRAM_ALLOWED_USER_IDS: optional, comma-separated user ids
- DB_PATH: optional sqlite path (default: ./data/expenses.db)
- LOG_LEVEL: optional (default: INFO)

Run:
  python3 run.py
"""

from __future__ import annotations

import logging

from bot import build_app
from config import load_dotenv, load_settings


def main() -> None:
    load_dotenv()
    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = build_app(settings)
    logging.getLogger("expenses-bot").info("Bot started (polling).")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

