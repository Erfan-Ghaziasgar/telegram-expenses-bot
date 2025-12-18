from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from telegram import BotCommand, ReplyKeyboardMarkup

DIRECTION_LABELS: Final[dict[str, str]] = {
    "expense": "Expense",
    "payable": "Payable (you owe)",
    "receivable": "Receivable (owed to you)",
}

HELP_TEXT: Final[str] = """\
To add a new record:
- Run /add (or tap it in the menu)
- Then follow the guided steps: type → counterparty → amount → description → confirm
Note: for privacy, this bot works only in private chats.

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
        ["/add"],
        ["/week", "/month"],
        ["/last", "/undo"],
        ["/id", "/help", "/hide"],
    ],
    resize_keyboard=True,
)

BOT_COMMANDS: Final[list[BotCommand]] = [
    BotCommand("start", "Start guided add"),
    BotCommand("help", "Show help"),
    BotCommand("add", "Add a new record (guided)"),
    BotCommand("id", "Show your Telegram user id"),
    BotCommand("menu", "Show command buttons"),
    BotCommand("hide", "Hide command buttons"),
    BotCommand("last", "Show recent records (optional: /last 10)"),
    BotCommand("undo", "Delete last record"),
    BotCommand("delete", "Delete a record by id (e.g. /delete 12)"),
    BotCommand("edit", "Edit a record (e.g. /edit 12)"),
    BotCommand("cancel", "Cancel the current operation"),
    BotCommand("week", "Weekly summary"),
    BotCommand("month", "Monthly summary"),
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
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return value[:19].replace("T", " ")

