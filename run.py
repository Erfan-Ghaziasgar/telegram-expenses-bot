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

import logging
import os
from pathlib import Path

def _load_env_file() -> None:
    """
    Minimal .env loader (no extra dependency). Existing environment variables win.
    """
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if (
            len(value) >= 2
            and value[0] in ("'", '"')
            and value[-1] == value[0]
        ):
            value = value[1:-1]
        os.environ[key] = value


_load_env_file()

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from db import format_summary_text, get_month_summary, get_week_summary, insert_transaction
from parser import parse_message

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("expenses-bot")

def _get_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing bot token. Set TELEGRAM_BOT_TOKEN (or BOT_TOKEN) environment variable."
        )
    return token


def _allowed_user_ids() -> set[int] | None:
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS")
    if not raw:
        return None
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.add(int(part))
    return ids


ALLOWED_USER_IDS = _allowed_user_ids()


def _is_allowed(update: Update) -> bool:
    if ALLOWED_USER_IDS is None:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED_USER_IDS)


HELP_TEXT = """\
Send a message like:
- 100 تومن پول نون
- 220 تومن به ممد باید بدم
- ۱۵۰ تومن ممد باید بهم بده

Commands:
/id - show your Telegram user id
/week - weekly summary
/month - monthly summary
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if not update.message:
        return
    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return
    summary = get_week_summary(user_id=user_id)
    await update.message.reply_text(format_summary_text(summary))


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if not update.message:
        return
    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return
    summary = get_month_summary(user_id=user_id)
    await update.message.reply_text(format_summary_text(summary))

async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user = update.effective_user
    if not user:
        return
    await update.message.reply_text(f"Your user id: {user.id}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return

    try:
        parsed = parse_message(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("I couldn't find an amount. Try:\n" + HELP_TEXT)
        return
    except Exception:
        logger.exception("parse_message failed")
        await update.message.reply_text("Sorry, I couldn't parse that message.")
        return

    try:
        insert_transaction(parsed, user_id=user_id)
    except Exception:
        logger.exception("insert_transaction failed")
        await update.message.reply_text("Sorry, I couldn't save that right now.")
        return

    amount = parsed.get("amount")
    direction = parsed.get("direction")
    person = parsed.get("person") or "-"
    description = parsed.get("description") or "-"

    await update.message.reply_text(
        "\n".join(
            [
                "Saved.",
                f"Amount: {amount}",
                f"Type: {direction}",
                f"Person: {person}",
                f"Description: {description}",
            ]
        )
    )


def main() -> None:
    token = _get_token()
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started (polling).")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
