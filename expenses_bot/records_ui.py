from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .ui import SYMBOLS, fmt_amount, fmt_created_at, fmt_direction

TX_CALLBACK_PREFIX = "tx:"


def format_recent_records_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"{SYMBOLS['records']} No records yet."

    ids = [f"#{int(r.get('id', 0) or 0)}" for r in rows]
    amounts = [fmt_amount(int(r.get("amount", 0) or 0)) for r in rows]
    directions = [fmt_direction(r.get("direction")) for r in rows]
    times = [fmt_created_at(r.get("created_at")) for r in rows]

    id_w = max(2, max((len(s) for s in ids), default=2))
    amt_w = max(1, max((len(s) for s in amounts), default=1))
    dir_w = max(4, max((len(s) for s in directions), default=4))

    lines: list[str] = [f"{SYMBOLS['records']} Recent records (your ids):"]
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
                    f"{SYMBOLS['edit']} Edit", callback_data=f"{TX_CALLBACK_PREFIX}edit:{tx_id}"
                ),
                InlineKeyboardButton(
                    f"{SYMBOLS['delete']} Delete", callback_data=f"{TX_CALLBACK_PREFIX}del:{tx_id}"
                ),
            ]
        )
    return InlineKeyboardMarkup(buttons)
