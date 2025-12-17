from __future__ import annotations

import logging
from typing import Any, Final

from telegram import BotCommand, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Settings
from db import (
    delete_transaction,
    format_summary_text,
    get_month_summary,
    get_recent_transactions,
    get_week_summary,
    insert_transaction,
    update_transaction,
)
from parser import parse_message

logger = logging.getLogger("expenses-bot")


HELP_TEXT: Final[str] = """\
Send a message like:
- 100 تومن پول نون
- ۲۲۰ به ممد
- 220 تومن به ممد باید بدم
- ۱۵۰ تومن ممد باید بهم بده

Commands:
/id - show your Telegram user id
/menu - show command buttons
/hide - hide command buttons
/last [n] - show recent records
/undo - delete last record
/delete <id> - delete by id
/edit <id> <new text> - edit by id
/week - weekly summary
/month - monthly summary
"""

COMMAND_KEYBOARD: Final[ReplyKeyboardMarkup] = ReplyKeyboardMarkup(
    [
        ["/week", "/month"],
        ["/last", "/undo"],
        ["/id", "/help", "/hide"],
    ],
    resize_keyboard=True,
)

BOT_COMMANDS: Final[list[BotCommand]] = [
    BotCommand("start", "Show help & menu"),
    BotCommand("help", "Show help"),
    BotCommand("id", "Show your Telegram user id"),
    BotCommand("menu", "Show command buttons"),
    BotCommand("hide", "Hide command buttons"),
    BotCommand("last", "Show recent records (optional: /last 10)"),
    BotCommand("undo", "Delete last record"),
    BotCommand("delete", "Delete a record by id (e.g. /delete 12)"),
    BotCommand("edit", "Edit a record (e.g. /edit 12 <text>)"),
    BotCommand("week", "Weekly summary"),
    BotCommand("month", "Monthly summary"),
]


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    settings = context.application.bot_data.get("settings")
    if not isinstance(settings, Settings):
        raise RuntimeError("Settings not initialized")
    return settings


def _db_pool(context: ContextTypes.DEFAULT_TYPE) -> Any:
    pool = context.application.bot_data.get("db_pool")
    if pool is None:
        raise RuntimeError("DB pool not initialized")
    return pool


def _user_id(update: Update) -> int | None:
    user = update.effective_user
    return user.id if user else None


def _is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    allowed_user_ids = _settings(context).allowed_user_ids
    if allowed_user_ids is None:
        return True
    uid = _user_id(update)
    return uid is not None and uid in allowed_user_ids


async def _reply(update: Update, text: str, *, reply_markup=None) -> None:
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    await _reply(update, HELP_TEXT, reply_markup=COMMAND_KEYBOARD)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    await _reply(update, HELP_TEXT, reply_markup=COMMAND_KEYBOARD)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await _reply(update, f"Your user id: {user.id}")


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    uid = _user_id(update)
    if uid is None:
        return
    summary = await get_week_summary(user_id=uid, pool=_db_pool(context))
    await _reply(update, format_summary_text(summary))


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    uid = _user_id(update)
    if uid is None:
        return
    summary = await get_month_summary(user_id=uid, pool=_db_pool(context))
    await _reply(update, format_summary_text(summary))

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    await _reply(update, "Menu:", reply_markup=COMMAND_KEYBOARD)


async def hide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    await _reply(update, "Menu hidden.", reply_markup=ReplyKeyboardRemove())


async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    uid = _user_id(update)
    if uid is None:
        return

    limit = 5
    if context.args:
        try:
            limit = int(context.args[0])
        except ValueError:
            await _reply(update, "Usage: /last [count]")
            return

    rows = await get_recent_transactions(user_id=uid, limit=limit, pool=_db_pool(context))
    if not rows:
        await _reply(update, "No records yet.")
        return

    lines = ["Recent records:"]
    for row in rows:
        person = row.get("person") or "-"
        desc = row.get("description") or "-"
        created_at = (row.get("created_at") or "")[:19].replace("T", " ")
        lines.append(
            f"#{row['id']} | {row['amount']} | {row['direction']} | {person} | {desc} | {created_at}"
        )
    await _reply(update, "\n".join(lines))


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    uid = _user_id(update)
    if uid is None:
        return

    rows = await get_recent_transactions(user_id=uid, limit=1, pool=_db_pool(context))
    if not rows:
        await _reply(update, "Nothing to undo.")
        return
    tx = rows[0]
    if await delete_transaction(user_id=uid, tx_id=int(tx["id"]), pool=_db_pool(context)):
        await _reply(update, f"Deleted last record: #{tx['id']}")
    else:
        await _reply(update, "Couldn't delete the last record.")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    uid = _user_id(update)
    if uid is None:
        return
    if not context.args:
        await _reply(update, "Usage: /delete <id>")
        return
    try:
        tx_id = int(context.args[0])
    except ValueError:
        await _reply(update, "Usage: /delete <id>")
        return

    if await delete_transaction(user_id=uid, tx_id=tx_id, pool=_db_pool(context)):
        await _reply(update, f"Deleted: #{tx_id}")
    else:
        await _reply(update, "Not found (or not yours).")


async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    uid = _user_id(update)
    if uid is None:
        return
    if len(context.args) < 2:
        await _reply(update, "Usage: /edit <id> <new text>")
        return

    try:
        tx_id = int(context.args[0])
    except ValueError:
        await _reply(update, "Usage: /edit <id> <new text>")
        return

    new_text = " ".join(context.args[1:]).strip()
    if not new_text:
        await _reply(update, "Usage: /edit <id> <new text>")
        return

    try:
        parsed = parse_message(new_text)
    except ValueError:
        await _reply(update, "I couldn't find an amount in the new text.")
        return
    except Exception:
        logger.exception("parse_message failed")
        await _reply(update, "Sorry, I couldn't parse that message.")
        return

    if not await update_transaction(parsed, user_id=uid, tx_id=tx_id, pool=_db_pool(context)):
        await _reply(update, "Not found (or not yours).")
        return

    amount = parsed.get("amount")
    direction = parsed.get("direction")
    person = parsed.get("person") or "-"
    description = parsed.get("description") or "-"
    await _reply(
        update,
        "\n".join(
            [
                f"Updated: #{tx_id}",
                f"Amount: {amount}",
                f"Type: {direction}",
                f"Person: {person}",
                f"Description: {description}",
            ]
        ),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not update.message or not update.message.text:
        return

    uid = _user_id(update)
    if uid is None:
        return

    try:
        parsed = parse_message(update.message.text.strip())
    except ValueError:
        await _reply(update, "I couldn't find an amount. Try:\n" + HELP_TEXT)
        return
    except Exception:
        logger.exception("parse_message failed")
        await _reply(update, "Sorry, I couldn't parse that message.")
        return

    try:
        tx_id = await insert_transaction(
            parsed,
            user_id=uid,
            pool=_db_pool(context),
            telegram_update_id=update.update_id if update.update_id is not None else None,
        )
    except Exception:
        logger.exception("insert_transaction failed")
        await _reply(update, "Sorry, I couldn't save that right now.")
        return

    amount = parsed.get("amount")
    direction = parsed.get("direction")
    person = parsed.get("person") or "-"
    description = parsed.get("description") or "-"

    await _reply(
        update,
        "\n".join(
            [
                "Saved.",
                f"ID: {tx_id}",
                f"Amount: {amount}",
                f"Type: {direction}",
                f"Person: {person}",
                f"Description: {description}",
            ]
        ),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Something went wrong. Please try again.")

async def _post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        logger.exception("Failed to set bot commands")

def build_app(settings: Settings, *, db_pool: Any) -> Application:
    app = ApplicationBuilder().token(settings.token).post_init(_post_init).build()
    app.bot_data["settings"] = settings
    app.bot_data["db_pool"] = db_pool

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("hide", hide))
    app.add_handler(CommandHandler("last", last))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("edit", edit))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)
    return app
