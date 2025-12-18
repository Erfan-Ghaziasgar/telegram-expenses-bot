from __future__ import annotations

import logging
import re
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from .config import Settings
from .db import (
    clear_user_flow,
    delete_transaction,
    format_summary_text_pretty,
    get_user_flow,
    get_month_summary,
    get_recent_transactions,
    get_transaction,
    get_week_summary,
    insert_transaction,
    set_user_flow,
    update_transaction,
)

from .flow import (
    ADD_CALLBACK_PREFIX,
    FLOW_KEY,
    cancel_keyboard,
    clean_description_input,
    clean_person_input,
    format_saved,
    new_flow,
    parse_amount_only,
    step_prompt,
    type_keyboard,
)
from .records_ui import TX_CALLBACK_PREFIX, build_recent_records_keyboard, format_recent_records_text
from .ui import COMMAND_KEYBOARD, HELP_TEXT, SYMBOLS

logger = logging.getLogger("expenses-bot")

_GREETING_RE = re.compile(r"^\s*(hi|hello|hey)\b", re.IGNORECASE)
_PROBABLY_COMMAND_RE = re.compile(
    r"^\s*(?:[.]+|\d+\s*[.])\s*(edit|delete|last|week|month|help|menu|hide|undo|id|add|cancel|wipe_all)\b",
    re.IGNORECASE,
)


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


def _is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == "private")


async def _reply(update: Update, text: str, *, reply_markup=None) -> None:
    msg = update.effective_message
    if msg:
        await msg.reply_text(text, reply_markup=reply_markup)


async def _require_private_chat(update: Update) -> bool:
    if _is_private_chat(update):
        return True
    await _reply(update, "For privacy, please use this bot in a private chat.")
    return False


async def _edit_or_reply_query_message(query, text: str, *, reply_markup=None) -> None:
    if not query or not query.message:
        return
    try:
        await query.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        await query.message.reply_text(text, reply_markup=reply_markup)


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    return int(chat.id) if chat and chat.id is not None else None


async def _load_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, *, user_id: int) -> dict[str, Any] | None:
    chat_id = _chat_id(update)
    return await get_user_flow(user_id=user_id, chat_id=chat_id, pool=_db_pool(context))


async def _save_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    flow: dict[str, Any],
) -> None:
    chat_id = _chat_id(update)
    await set_user_flow(flow, user_id=user_id, chat_id=chat_id, pool=_db_pool(context))


async def _clear_flow_db(context: ContextTypes.DEFAULT_TYPE, *, user_id: int) -> None:
    await clear_user_flow(user_id=user_id, pool=_db_pool(context))


async def _save_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    flow: dict[str, Any],
) -> None:
    direction = flow.get("direction")
    amount = flow.get("amount")
    mode = flow.get("mode") or "add"

    if direction not in {"expense", "payable", "receivable"} or not isinstance(amount, int):
        await _reply(update, "Missing required fields. Use /cancel.", reply_markup=cancel_keyboard())
        return

    if direction in {"payable", "receivable"} and not (flow.get("person") or "").strip():
        flow["step"] = "person"
        await _save_flow(update, context, user_id=user_id, flow=flow)
        text, markup = step_prompt(flow)
        await _reply(update, "Counterparty is required.", reply_markup=markup)
        return

    person = (flow.get("person") or "").strip() or None
    if direction == "expense":
        person = None

    parsed = {
        "amount": int(amount),
        "direction": str(direction),
        "person": person,
        "description": (flow.get("description") or "").strip(),
        "raw": "guided",
    }

    try:
        if mode == "edit":
            tx_id = flow.get("tx_id")
            if not isinstance(tx_id, int):
                await _reply(update, "Invalid record id for edit. Use /cancel.", reply_markup=cancel_keyboard())
                return
            ok = await update_transaction(parsed, user_id=user_id, tx_id=int(tx_id), pool=_db_pool(context))
            if not ok:
                await _reply(update, "Not found (or not yours).")
                await _clear_flow_db(context, user_id=user_id)
                return
            saved_id = int(tx_id)
        else:
            saved_id = await insert_transaction(
                parsed,
                user_id=user_id,
                pool=_db_pool(context),
                telegram_update_id=update.update_id if update.update_id is not None else None,
                telegram_chat_id=update.effective_chat.id if update.effective_chat else None,
                telegram_message_id=update.effective_message.message_id
                if update.effective_message and update.effective_message.message_id is not None
                else None,
            )
    except Exception:
        logger.exception("save_transaction failed")
        await _reply(update, "Sorry, I couldn't save that right now. Try again.")
        return

    await _clear_flow_db(context, user_id=user_id)
    await _reply(update, format_saved(flow, tx_id=int(saved_id)))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
        return
    uid = _user_id(update)
    if uid is None:
        return
    flow = new_flow(mode="add")
    await _save_flow(update, context, user_id=uid, flow=flow)
    text, markup = step_prompt(flow)
    await _reply(update, text, reply_markup=markup)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
        return
    await _reply(update, HELP_TEXT, reply_markup=COMMAND_KEYBOARD)


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
        return
    uid = _user_id(update)
    if uid is None:
        return
    flow = new_flow(mode="add")
    await _save_flow(update, context, user_id=uid, flow=flow)
    text, markup = step_prompt(flow)
    await _reply(update, text, reply_markup=markup)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_chat(update):
        return
    user = update.effective_user
    if not user:
        return
    await _reply(update, f"Your user id: {user.id}")


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
        return
    uid = _user_id(update)
    if uid is None:
        return
    summary = await get_week_summary(user_id=uid, pool=_db_pool(context))
    await _reply(update, format_summary_text_pretty(summary, title="Weekly summary", max_days=7))


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
        return
    uid = _user_id(update)
    if uid is None:
        return
    summary = await get_month_summary(user_id=uid, pool=_db_pool(context))
    await _reply(update, format_summary_text_pretty(summary, title="Monthly summary", max_days=10))


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
        return
    await _reply(update, f"{SYMBOLS['menu']} Menu:", reply_markup=COMMAND_KEYBOARD)


async def hide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
        return
    from telegram import ReplyKeyboardRemove

    await _reply(update, "Menu hidden.", reply_markup=ReplyKeyboardRemove())


async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
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
    if not await _require_private_chat(update):
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
    if not await _require_private_chat(update):
        return
    uid = _user_id(update)
    if uid is None:
        await _reply(update, "Canceled.")
        return
    cleared = False
    flow = await _load_flow(update, context, user_id=uid)
    if flow is not None:
        cleared = True
    await _clear_flow_db(context, user_id=uid)
    await _reply(update, "Canceled." if cleared else "Nothing to cancel.")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
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
    if not await _require_private_chat(update):
        return
    uid = _user_id(update)
    if uid is None:
        return
    if len(context.args) != 1:
        await _reply(update, "Usage: /edit <id>")
        return

    try:
        tx_id = int(context.args[0])
    except ValueError:
        await _reply(update, "Usage: /edit <id>")
        return

    tx = await get_transaction(user_id=uid, tx_id=tx_id, pool=_db_pool(context))
    if not tx:
        await _reply(update, "Not found (or not yours).")
        return

    flow = new_flow(mode="edit", tx_id=tx_id)
    flow["direction"] = tx.get("direction")
    flow["person"] = (tx.get("person") or "").strip() or None
    flow["amount"] = int(tx.get("amount") or 0)
    flow["description"] = (tx.get("description") or "").strip() or None
    flow["step"] = "description"
    await _save_flow(update, context, user_id=uid, flow=flow)
    text, markup = step_prompt(flow)
    await _reply(update, text, reply_markup=markup)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    if not await _require_private_chat(update):
        return
    if not update.message or not update.message.text:
        return

    uid = _user_id(update)
    if uid is None:
        return

    raw_text = update.message.text.strip()
    if _PROBABLY_COMMAND_RE.search(raw_text):
        await _reply(
            update,
            "\n".join(
                [
                    "It looks like you tried to use a command.",
                    "Use /menu or one of these:",
                    f"- {SYMBOLS['records']} /last (then tap Edit/Delete buttons)",
                    "- /edit <id>",
                    "- /delete <id>",
                    f"- {SYMBOLS['new']} /add",
                ]
            ),
        )
        return

    flow = await _load_flow(update, context, user_id=uid)
    if flow is not None:
        step = flow.get("step")

        if step == "choose_type":
            await _reply(update, "Please choose the type using the buttons:", reply_markup=type_keyboard())
            return

        if step == "person":
            person = clean_person_input(raw_text)
            if not person:
                await _reply(
                    update,
                    "\n".join(
                        [
                            "Invalid name format.",
                            "Send a short name (no numbers). Example: Ali",
                        ]
                    ),
                    reply_markup=cancel_keyboard(),
                )
                return
            flow["person"] = person
            flow["step"] = "amount"
            await _save_flow(update, context, user_id=uid, flow=flow)
            text, markup = step_prompt(flow)
            await _reply(update, text, reply_markup=markup)
            return

        if step == "amount":
            try:
                amount = parse_amount_only(raw_text)
            except ValueError:
                await _reply(
                    update,
                    "\n".join(
                        [
                            "Invalid amount format.",
                            "Send only the number (no words). Examples:",
                            "- 400",
                            "- 400000",
                            "- 150,000",
                        ]
                    ),
                    reply_markup=cancel_keyboard(),
                )
                return
            flow["amount"] = int(amount)
            flow["step"] = "description"
            await _save_flow(update, context, user_id=uid, flow=flow)
            text, markup = step_prompt(flow)
            await _reply(update, text, reply_markup=markup)
            return

        if step == "description":
            flow["description"] = clean_description_input(raw_text) or None
            await _save_and_reply(update, context, user_id=uid, flow=flow)
            return

        await _clear_flow_db(context, user_id=uid)

    if _GREETING_RE.search(raw_text):
        await _reply(update, HELP_TEXT, reply_markup=COMMAND_KEYBOARD)
        return

    await _reply(update, "To add a new record, run /add.", reply_markup=COMMAND_KEYBOARD)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Something went wrong. Please try again.")


async def add_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data.strip()
    if not data.startswith(ADD_CALLBACK_PREFIX):
        await query.answer()
        return

    if not _is_private_chat(update):
        await query.answer()
        uid = _user_id(update)
        if uid is not None:
            await _clear_flow_db(context, user_id=uid)
        await _edit_or_reply_query_message(query, "For privacy, please use this bot in a private chat.")
        return

    await query.answer()

    uid = _user_id(update)
    if uid is None:
        await _edit_or_reply_query_message(query, "Error: missing user id.")
        return

    flow = await _load_flow(update, context, user_id=uid)
    if flow is None:
        flow = new_flow(mode="add")
        await _save_flow(update, context, user_id=uid, flow=flow)

    action = data[len(ADD_CALLBACK_PREFIX) :]

    if action == "cancel":
        await _clear_flow_db(context, user_id=uid)
        await _edit_or_reply_query_message(query, "Canceled.")
        return

    if action.startswith("type:"):
        if flow.get("step") != "choose_type":
            text, markup = step_prompt(flow)
            await _edit_or_reply_query_message(query, text, reply_markup=markup)
            return
        _, _, direction = action.partition(":")
        if direction not in {"expense", "payable", "receivable"}:
            await _edit_or_reply_query_message(query, "Invalid type. Please choose again:", reply_markup=type_keyboard())
            return

        flow["direction"] = direction
        if direction == "expense":
            flow["person"] = None

        if direction in {"payable", "receivable"}:
            flow["step"] = "person"
        else:
            flow["step"] = "amount"

        await _save_flow(update, context, user_id=uid, flow=flow)
        text, markup = step_prompt(flow)
        await _edit_or_reply_query_message(query, text, reply_markup=markup)
        return

    if action == "person:ok":
        if flow.get("step") != "person":
            text, markup = step_prompt(flow)
            await _edit_or_reply_query_message(query, text, reply_markup=markup)
            return
        if flow.get("direction") not in {"payable", "receivable"}:
            flow["step"] = "choose_type"
            text, markup = step_prompt(flow)
            await _edit_or_reply_query_message(query, text, reply_markup=markup)
            return
        person = (flow.get("person") or "").strip()
        if not person:
            flow["step"] = "person"
            text, markup = step_prompt(flow)
            await _edit_or_reply_query_message(query, text, reply_markup=markup)
            return
        flow["step"] = "amount"
        await _save_flow(update, context, user_id=uid, flow=flow)
        text, markup = step_prompt(flow)
        await _edit_or_reply_query_message(query, text, reply_markup=markup)
        return

    if action == "amount:ok":
        if flow.get("step") != "amount":
            text, markup = step_prompt(flow)
            await _edit_or_reply_query_message(query, text, reply_markup=markup)
            return
        if not isinstance(flow.get("amount"), int):
            flow["step"] = "amount"
            text, markup = step_prompt(flow)
            await _edit_or_reply_query_message(query, text, reply_markup=markup)
            return
        flow["step"] = "description"
        await _save_flow(update, context, user_id=uid, flow=flow)
        text, markup = step_prompt(flow)
        await _edit_or_reply_query_message(query, text, reply_markup=markup)
        return

    if action.startswith("desc:"):
        if flow.get("step") != "description":
            text, markup = step_prompt(flow)
            await _edit_or_reply_query_message(query, text, reply_markup=markup)
            return
        _, _, how = action.partition(":")
        existing = (flow.get("description") or "").strip()
        if how == "skip":
            flow["description"] = None
        elif how == "keep":
            flow["description"] = existing or None
        elif how == "clear":
            flow["description"] = None
        else:
            await _edit_or_reply_query_message(query, "Invalid option.")
            return
        await _save_and_reply(update, context, user_id=uid, flow=flow)
        return

    text, markup = step_prompt(flow)
    await _edit_or_reply_query_message(query, text, reply_markup=markup)


async def tx_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        return
    query = update.callback_query
    if not query or not query.data:
        return

    if not _is_private_chat(update):
        await query.answer()
        await _edit_or_reply_query_message(query, "For privacy, please use this bot in a private chat.")
        return

    uid = _user_id(update)
    if uid is None:
        await query.answer()
        return

    data = query.data.strip()
    if not data.startswith(TX_CALLBACK_PREFIX):
        await query.answer()
        return

    action_and_id = data[len(TX_CALLBACK_PREFIX) :]
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
        tx = await get_transaction(user_id=uid, tx_id=tx_id, pool=_db_pool(context))
        if not tx:
            await query.message.reply_text("Not found (or not yours).")
            return

        flow = new_flow(mode="edit", tx_id=int(tx_id))
        flow["direction"] = tx.get("direction")
        flow["person"] = (tx.get("person") or "").strip() or None
        flow["amount"] = int(tx.get("amount") or 0)
        flow["description"] = (tx.get("description") or "").strip() or None
        flow["step"] = "description"
        await _save_flow(update, context, user_id=uid, flow=flow)

        text, markup = step_prompt(flow)
        await query.message.reply_text(
            text,
            reply_markup=markup,
        )
        return
