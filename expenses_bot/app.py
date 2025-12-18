from __future__ import annotations

import logging
from typing import Any

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Settings

from .handlers import (
    add_buttons,
    add_cmd,
    cancel,
    delete_cmd,
    edit,
    error_handler,
    handle_text,
    help_cmd,
    hide,
    last,
    menu,
    month,
    my_id,
    start,
    tx_buttons,
    undo,
    week,
)
from .ui import BOT_COMMANDS

logger = logging.getLogger("expenses-bot")


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
    app.add_handler(CommandHandler("add", add_cmd))
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
    app.add_handler(CallbackQueryHandler(add_buttons, pattern=r"^add:"))
    app.add_handler(CallbackQueryHandler(tx_buttons, pattern=r"^tx:(edit|del):\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)
    return app
