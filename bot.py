from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Final

from telegram import (
    BotCommand,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Settings
from db import (
    delete_transaction,
    format_summary_text_pretty,
    get_month_summary,
    get_recent_transactions,
    get_transaction,
    get_week_summary,
    insert_transaction,
    update_transaction,
)
from parser import parse_message

logger = logging.getLogger("expenses-bot")

_GREETING_RE = re.compile(r"^\s*(سلام|salam|hi|hello|hey)\b", re.IGNORECASE)

_DIRECTION_LABELS: Final[dict[str, str]] = {
    "expense": "Expense",
    "payable": "You owe",
    "receivable": "Owed to you",
}

_TX_CALLBACK_PREFIX: Final[str] = "tx:"

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
/delete <id> - delete by id (your own ids)
/edit <id> <new text> - edit by id (your own ids)
/cancel - cancel an in-progress edit
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
    BotCommand("cancel", "Cancel the current edit"),
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
    await _reply(update, format_summary_text_pretty(summary, title="Weekly summary", max_days=7))


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    uid = _user_id(update)
    if uid is None:
        return
    summary = await get_month_summary(user_id=uid, pool=_db_pool(context))
    await _reply(update, format_summary_text_pretty(summary, title="Monthly summary", max_days=10))


def _fmt_amount(value: int) -> str:
    return f"{int(value):,}"


def _fmt_direction(direction: str | None) -> str:
    if not direction:
        return "-"
    return _DIRECTION_LABELS.get(direction, direction)


def _fmt_created_at(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return value[:19].replace("T", " ")


def format_recent_records_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No records yet."

    ids = [f"#{int(r.get('id', 0) or 0)}" for r in rows]
    amounts = [_fmt_amount(int(r.get("amount", 0) or 0)) for r in rows]
    directions = [_fmt_direction(r.get("direction")) for r in rows]
    times = [_fmt_created_at(r.get("created_at")) for r in rows]

    id_w = max(2, max((len(s) for s in ids), default=2))
    amt_w = max(1, max((len(s) for s in amounts), default=1))
    dir_w = max(4, max((len(s) for s in directions), default=4))

    lines: list[str] = ["Recent records (your ids):"]
    for r, tx_id, amount, direction, created_at in zip(rows, ids, amounts, directions, times):
        lines.append(
            f"{tx_id.ljust(id_w)}  {amount.rjust(amt_w)}  {direction.ljust(dir_w)}  {created_at}"
        )

        person = (r.get("person") or "").strip()
        desc = (r.get("description") or "").strip()
        detail_parts = [p for p in (person, desc) if p]
        if detail_parts:
            lines.append(f"    {' — '.join(detail_parts)}")
        lines.append("")

    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def build_recent_records_keyboard(rows: list[dict[str, Any]], *, max_rows: int = 10):
    if not rows:
        return None
    max_rows = max(1, min(int(max_rows), 20))
    buttons: list[list[InlineKeyboardButton]] = []
    for row in rows[:max_rows]:
        tx_id = int(row["id"])
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Edit #{tx_id}",
                    callback_data=f"{_TX_CALLBACK_PREFIX}edit:{tx_id}",
                ),
                InlineKeyboardButton(
                    f"Delete #{tx_id}", callback_data=f"{_TX_CALLBACK_PREFIX}del:{tx_id}"
                ),
            ]
        )
    return InlineKeyboardMarkup(buttons)


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
    context.user_data["last_limit"] = limit
    button_rows = min(limit, 10)
    await _reply(
        update,
        format_recent_records_text(rows),
        reply_markup=build_recent_records_keyboard(rows, max_rows=button_rows),
    )


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


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if context.user_data.pop("pending_edit_tx_id", None) is None:
        await _reply(update, "Nothing to cancel.")
        return
    await _reply(update, "Canceled.")


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

    pending_edit_tx_id = context.user_data.get("pending_edit_tx_id")
    if pending_edit_tx_id is not None:
        try:
            parsed = parse_message(update.message.text.strip())
        except ValueError:
            await _reply(
                update,
                "I couldn't find an amount. Reply with a full message like `700 پول چلو`, or /cancel.",
            )
            return
        except Exception:
            logger.exception("parse_message failed")
            await _reply(update, "Sorry, I couldn't parse that. Try again or /cancel.")
            return

        ok = await update_transaction(
            parsed,
            user_id=uid,
            tx_id=int(pending_edit_tx_id),
            pool=_db_pool(context),
        )
        if not ok:
            context.user_data.pop("pending_edit_tx_id", None)
            await _reply(update, "Not found (or not yours).")
            return

        context.user_data.pop("pending_edit_tx_id", None)
        amount = parsed.get("amount")
        direction = parsed.get("direction")
        person = parsed.get("person") or "-"
        description = parsed.get("description") or "-"
        await _reply(
            update,
            "\n".join(
                [
                    f"Updated: #{int(pending_edit_tx_id)}",
                    f"Amount: {amount}",
                    f"Type: {direction}",
                    f"Person: {person}",
                    f"Description: {description}",
                ]
            ),
        )
        return

    try:
        parsed = parse_message(update.message.text.strip())
    except ValueError:
        text = update.message.text.strip()
        if _GREETING_RE.search(text):
            await _reply(
                update,
                "سلام!\n"
                "یک پیام با مبلغ بفرست (مثلاً: 100 تومن پول نون) یا /help رو بزن.",
            )
        else:
            await _reply(update, "I couldn't find an amount. Send `/help` for examples.")
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
            telegram_chat_id=update.effective_chat.id if update.effective_chat else None,
            telegram_message_id=update.message.message_id if update.message else None,
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


async def tx_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    query = update.callback_query
    if not query or not query.data:
        return

    uid = _user_id(update)
    if uid is None:
        await query.answer()
        return

    data = query.data.strip()
    if not data.startswith(_TX_CALLBACK_PREFIX):
        await query.answer()
        return

    action_and_id = data[len(_TX_CALLBACK_PREFIX) :]
    action, _, tx_id_str = action_and_id.partition(":")
    try:
        tx_id = int(tx_id_str)
    except ValueError:
        await query.answer()
        return

    await query.answer()
    if not query.message:
        return

    if action == "del":
        ok = await delete_transaction(user_id=uid, tx_id=tx_id, pool=_db_pool(context))
        if not ok:
            await query.message.reply_text("Not found (or not yours).")
            return

        limit = int(context.user_data.get("last_limit", 5) or 5)
        rows = await get_recent_transactions(user_id=uid, limit=limit, pool=_db_pool(context))
        text = format_recent_records_text(rows)
        markup = build_recent_records_keyboard(rows, max_rows=min(limit, 10))
        try:
            await query.message.edit_text(text, reply_markup=markup)
        except Exception:
            await query.message.reply_text(text, reply_markup=markup)
        return

    if action == "edit":
        context.user_data["pending_edit_tx_id"] = tx_id
        tx = await get_transaction(user_id=uid, tx_id=tx_id, pool=_db_pool(context))
        if not tx:
            context.user_data.pop("pending_edit_tx_id", None)
            await query.message.reply_text("Not found (or not yours).")
            return

        person = (tx.get("person") or "-").strip() or "-"
        description = (tx.get("description") or "-").strip() or "-"
        direction = _fmt_direction(tx.get("direction"))
        created_at = _fmt_created_at(tx.get("created_at"))
        amount = _fmt_amount(int(tx.get("amount", 0) or 0))

        await query.message.reply_text(
            "\n".join(
                [
                    f"Editing #{tx_id}",
                    f"Current: {amount} | {direction} | {created_at}",
                    f"Details: {person} — {description}",
                    "",
                    "Reply with the new text (same format as a normal message), or /cancel.",
                ]
            ),
            reply_markup=ForceReply(selective=True),
        )
        return


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
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(CallbackQueryHandler(tx_buttons, pattern=r"^tx:(edit|del):\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)
    return app
