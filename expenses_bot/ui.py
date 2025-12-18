from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from telegram import BotCommand, ReplyKeyboardMarkup

from .dates import format_dual_datetime_utc

SYMBOLS: Final[dict[str, str]] = {
    "new": "➕",
    "cancel": "✖️",
    "ok": "✅",
    "edit": "✏️",
    "delete": "🗑️",
    "menu": "🧭",
    "help": "ℹ️",
    "id": "🪪",
    "week": "📅",
    "month": "🗓️",
    "records": "🧾",
    "totals": "📊",
    "trend": "📈",
    "expense": "💸",
    "payable": "📤",
    "receivable": "📥",
    "net": "🧮",
    "person": "👤",
    "note": "📝",
    "amount": "💰",
}

DIRECTION_LABELS: Final[dict[str, str]] = {
    "expense": f"{SYMBOLS['expense']} Expense",
    "payable": f"{SYMBOLS['payable']} Payable (you owe)",
    "receivable": f"{SYMBOLS['receivable']} Receivable (owed to you)",
}

HELP_TEXT: Final[str] = """\
➕ Add a record
- Run /add (or tap the button)
- Follow the guided steps (type → counterparty → amount → description)
- The bot saves automatically at the end

Privacy: this bot works only in private chats.

Commands:
/add - start a new record (guided)
/id - show your Telegram user id
/menu - show command buttons
/hide - hide command buttons
/last [n] - show recent records
/undo - delete last record
/delete <id> - delete by id (your own ids)
/edit <id> - edit a record by id (guided)
/cancel - cancel the current operation
/week - weekly summary
/month - monthly summary
"""

COMMAND_KEYBOARD: Final[ReplyKeyboardMarkup] = ReplyKeyboardMarkup(
    [
        [f"{SYMBOLS['new']} /add"],
        ["/week", "/month"],
        ["/last", "/undo"],
        ["/id", "/help", "/hide"],
    ],
    resize_keyboard=True,
)

BOT_COMMANDS: Final[list[BotCommand]] = [
    BotCommand("start", "Start"),
    BotCommand("help", "Help"),
    BotCommand("add", "Add a record"),
    BotCommand("id", "Show your Telegram user id"),
    BotCommand("menu", "Show menu"),
    BotCommand("hide", "Hide menu"),
    BotCommand("last", "Show recent records (optional: /last 10)"),
    BotCommand("undo", "Delete last record"),
    BotCommand("delete", "Delete a record by id (e.g. /delete 12)"),
    BotCommand("edit", "Edit a record by id (e.g. /edit 12)"),
    BotCommand("cancel", "Cancel"),
    BotCommand("week", "Weekly summary (last 7 days)"),
    BotCommand("month", "Monthly summary (last 30 days)"),
]


def fmt_amount(value: int) -> str:
    return f"{int(value):,}"


def fmt_direction(direction: str | None) -> str:
    if not direction:
        return "-"
    return DIRECTION_LABELS.get(direction, direction)


def fmt_created_at(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        return format_dual_datetime_utc(dt)
    except Exception:
        return value[:19].replace("T", " ")
