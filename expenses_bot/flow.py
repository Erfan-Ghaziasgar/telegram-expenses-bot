from __future__ import annotations

import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .ui import fmt_amount, fmt_direction

FLOW_KEY = "tx_flow"
ADD_CALLBACK_PREFIX = "add:"

AMOUNT_ONLY_RE = re.compile(r"^\s*([0-9][0-9,\s_.]{0,24})\s*$")


def parse_amount_only(text: str) -> int:
    m = AMOUNT_ONLY_RE.match((text or "").strip())
    if not m:
        raise ValueError("Invalid amount format")
    digits = re.sub(r"[,\s_.]", "", m.group(1))
    if not digits.isdigit():
        raise ValueError("Invalid amount format")
    value = int(digits)
    if value < 0:
        raise ValueError("Amount must be >= 0")
    return value


def new_flow(*, mode: str, tx_id: int | None = None) -> dict[str, Any]:
    return {
        "mode": mode,  # add|edit
        "tx_id": tx_id,
        "step": "choose_type",  # choose_type|person|amount|description|confirm
        "direction": None,
        "person": None,
        "amount": None,
        "description": None,
    }


def get_flow(user_data: dict[str, Any]) -> dict[str, Any] | None:
    flow = user_data.get(FLOW_KEY)
    return flow if isinstance(flow, dict) else None


def clear_flow(user_data: dict[str, Any]) -> None:
    user_data.pop(FLOW_KEY, None)


def type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Expense", callback_data=f"{ADD_CALLBACK_PREFIX}type:expense"),
                InlineKeyboardButton("Payable", callback_data=f"{ADD_CALLBACK_PREFIX}type:payable"),
                InlineKeyboardButton(
                    "Receivable", callback_data=f"{ADD_CALLBACK_PREFIX}type:receivable"
                ),
            ],
            [InlineKeyboardButton("Cancel", callback_data=f"{ADD_CALLBACK_PREFIX}cancel")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"{ADD_CALLBACK_PREFIX}cancel")]])


def ok_cancel_keyboard(ok_action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("OK", callback_data=f"{ADD_CALLBACK_PREFIX}{ok_action}")],
            [InlineKeyboardButton("Cancel", callback_data=f"{ADD_CALLBACK_PREFIX}cancel")],
        ]
    )


def description_keyboard(*, has_existing: bool) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = [
        InlineKeyboardButton("Skip", callback_data=f"{ADD_CALLBACK_PREFIX}desc:skip")
    ]
    if has_existing:
        row.insert(0, InlineKeyboardButton("Keep", callback_data=f"{ADD_CALLBACK_PREFIX}desc:keep"))
        row.append(InlineKeyboardButton("Clear", callback_data=f"{ADD_CALLBACK_PREFIX}desc:clear"))
    return InlineKeyboardMarkup([row, [InlineKeyboardButton("Cancel", callback_data=f"{ADD_CALLBACK_PREFIX}cancel")]])


def confirm_keyboard(direction: str, *, mode: str = "add") -> InlineKeyboardMarkup:
    save_label = "Update" if mode == "edit" else "Save"
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(save_label, callback_data=f"{ADD_CALLBACK_PREFIX}confirm:save")],
        [
            InlineKeyboardButton("Change type", callback_data=f"{ADD_CALLBACK_PREFIX}confirm:edit:type"),
            InlineKeyboardButton(
                "Change amount", callback_data=f"{ADD_CALLBACK_PREFIX}confirm:edit:amount"
            ),
        ],
        [
            InlineKeyboardButton(
                "Change description", callback_data=f"{ADD_CALLBACK_PREFIX}confirm:edit:description"
            ),
            InlineKeyboardButton("Cancel", callback_data=f"{ADD_CALLBACK_PREFIX}cancel"),
        ],
    ]
    if direction in {"payable", "receivable"}:
        buttons.insert(
            2,
            [
                InlineKeyboardButton(
                    "Change counterparty", callback_data=f"{ADD_CALLBACK_PREFIX}confirm:edit:person"
                )
            ],
        )
    return InlineKeyboardMarkup(buttons)


def clean_person_input(text: str) -> str | None:
    name = re.sub(r"\s{2,}", " ", (text or "").strip())
    if not name:
        return None
    if "\n" in name or "\r" in name:
        return None
    if len(name) > 40:
        return None
    if re.search(r"\d", name):
        return None
    return name


def clean_description_input(text: str) -> str:
    desc = re.sub(r"\s{2,}", " ", (text or "").strip())
    if len(desc) > 200:
        desc = desc[:200].rstrip()
    return desc


def format_review(flow: dict[str, Any]) -> str:
    direction = flow.get("direction") or "-"
    person = (flow.get("person") or "-") if direction in {"payable", "receivable"} else "-"
    amount = flow.get("amount")
    description = (flow.get("description") or "-").strip() or "-"
    amt = fmt_amount(int(amount)) if isinstance(amount, int) else "-"
    return "\n".join(
        [
            "Review & confirm:",
            f"Type: {fmt_direction(str(direction))}",
            f"Counterparty: {person}",
            f"Amount: {amt}",
            f"Description: {description}",
        ]
    )


def format_saved(flow: dict[str, Any], *, tx_id: int) -> str:
    mode = flow.get("mode")
    direction = flow.get("direction") or "-"
    person = (flow.get("person") or "-") if direction in {"payable", "receivable"} else "-"
    amount = flow.get("amount")
    description = (flow.get("description") or "-").strip() or "-"
    amt = fmt_amount(int(amount)) if isinstance(amount, int) else "-"
    return "\n".join(
        [
            "Saved." if mode != "edit" else "Updated.",
            f"ID: #{int(tx_id)}",
            f"Type: {fmt_direction(str(direction))}",
            f"Counterparty: {person}",
            f"Amount: {amt}",
            f"Description: {description}",
        ]
    )


def _flow_step_meta(flow: dict[str, Any]) -> tuple[dict[str, int], int]:
    direction = flow.get("direction")
    if direction in {"payable", "receivable"}:
        return ({"choose_type": 1, "person": 2, "amount": 3, "description": 4}, 4)
    return ({"choose_type": 1, "amount": 2, "description": 3}, 3)


def _step_label(flow: dict[str, Any], step_key: str) -> str:
    numbers, total = _flow_step_meta(flow)
    n = numbers.get(step_key)
    if not n:
        return ""
    return f"Step {n}/{total}: "


def step_prompt(flow: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    step = flow.get("step")
    direction = flow.get("direction")

    if step == "choose_type":
        return (f"{_step_label(flow, 'choose_type')}Choose the type:", type_keyboard())

    if step == "person":
        suggested = (flow.get("person") or "").strip()
        if suggested:
            return (
                "\n".join(
                    [
                        f"{_step_label(flow, 'person')}Who is the counterparty?",
                        f"Suggested: {suggested}",
                        "If it's correct press OK, otherwise send the correct name.",
                    ]
                ),
                ok_cancel_keyboard("person:ok"),
            )
        return (
            "\n".join(
                [
                    f"{_step_label(flow, 'person')}Who is the counterparty?",
                    "Send only the name (example: Ali).",
                ]
            ),
            cancel_keyboard(),
        )

    if step == "amount":
        suggested_amount = flow.get("amount")
        if isinstance(suggested_amount, int):
            return (
                "\n".join(
                    [
                        f"{_step_label(flow, 'amount')}What's the amount?",
                        f"Suggested: {fmt_amount(suggested_amount)}",
                        "If it's correct press OK, otherwise send only the number (example: 400 or 150000).",
                    ]
                ),
                ok_cancel_keyboard("amount:ok"),
            )
        return (
            "\n".join(
                [
                    f"{_step_label(flow, 'amount')}What's the amount?",
                    "Send only the number (example: 400 or 150,000).",
                ]
            ),
            cancel_keyboard(),
        )

    if step == "description":
        existing_desc = (flow.get("description") or "").strip()
        return (
            "\n".join(
                [
                    f"{_step_label(flow, 'description')}Description (optional).",
                    "Send a short description (example: Pizza), or use the buttons.",
                    f"Current: {existing_desc or '-'}",
                ]
            ),
            description_keyboard(has_existing=bool(existing_desc)),
        )

    # confirm (or any unexpected state)
    if direction not in {"expense", "payable", "receivable"}:
        flow["step"] = "choose_type"
        return step_prompt(flow)
    return (
        format_review(flow),
        confirm_keyboard(str(direction), mode=str(flow.get("mode") or "add")),
    )

