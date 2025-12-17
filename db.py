from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

_SCHEMA_LOCK = asyncio.Lock()
_SCHEMA_READY = False


@dataclass
class Summary:
    start: str
    end: str
    totals_by_direction: Dict[str, int]
    totals_by_person: Dict[str, int]
    daily_totals: List[Tuple[str, int]]
    count: int


async def init_db(pool: asyncpg.Pool) -> None:
    """
    Creates tables/indexes if they don't exist.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    telegram_update_id BIGINT,
                    telegram_chat_id BIGINT,
                    telegram_message_id BIGINT,
                    amount INTEGER NOT NULL CHECK (amount >= 0),
                    direction TEXT NOT NULL CHECK (direction IN ('expense','payable','receivable')),
                    person TEXT,
                    description TEXT,
                    raw TEXT,
                    created_at TIMESTAMPTZ NOT NULL
                );
                """
            )
            # Migrations for older deployments
            await conn.execute(
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS telegram_update_id BIGINT;"
            )
            await conn.execute(
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT;"
            )
            await conn.execute(
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT;"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tx_user_time ON transactions(user_id, created_at DESC);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tx_user_person ON transactions(user_id, person);"
            )
            # Idempotency: prevent duplicates if Telegram retries the same update/message.
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_tx_telegram_update_id
                ON transactions(telegram_update_id)
                WHERE telegram_update_id IS NOT NULL;
                """
            )
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_tx_telegram_chat_message
                ON transactions(telegram_chat_id, telegram_message_id)
                WHERE telegram_chat_id IS NOT NULL AND telegram_message_id IS NOT NULL;
                """
            )
        _SCHEMA_READY = True


async def insert_transaction(
    parsed: Dict[str, Any],
    *,
    user_id: int,
    pool: asyncpg.Pool,
    created_at: Optional[datetime] = None,
    telegram_update_id: int | None = None,
    telegram_chat_id: int | None = None,
    telegram_message_id: int | None = None,
) -> int:
    """
    Insert one transaction. Returns inserted row id.
    """
    await init_db(pool)

    amount = int(parsed["amount"])
    direction = parsed["direction"]
    person = parsed.get("person")
    description = parsed.get("description") or ""
    raw = parsed.get("raw") or ""

    now = created_at or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO transactions
                (
                    user_id,
                    telegram_update_id,
                    telegram_chat_id,
                    telegram_message_id,
                    amount,
                    direction,
                    person,
                    description,
                    raw,
                    created_at
                )
            VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            int(user_id),
            int(telegram_update_id) if telegram_update_id is not None else None,
            int(telegram_chat_id) if telegram_chat_id is not None else None,
            int(telegram_message_id) if telegram_message_id is not None else None,
            amount,
            direction,
            person,
            description,
            raw,
            now,
        )
        if row:
            return int(row["id"])

        existing = None
        if telegram_update_id is not None:
            existing = await conn.fetchrow(
                "SELECT id FROM transactions WHERE telegram_update_id = $1 AND user_id = $2",
                int(telegram_update_id),
                int(user_id),
            )
        if existing is None and telegram_chat_id is not None and telegram_message_id is not None:
            existing = await conn.fetchrow(
                """
                SELECT id
                FROM transactions
                WHERE telegram_chat_id = $1 AND telegram_message_id = $2 AND user_id = $3
                """,
                int(telegram_chat_id),
                int(telegram_message_id),
                int(user_id),
            )
        if not existing:
            raise RuntimeError("Insert conflict but existing row not found")
        return int(existing["id"])


async def list_transactions(
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    pool: asyncpg.Pool,
) -> List[Dict[str, Any]]:
    """
    Return raw transactions in [start, end).
    """
    await init_db(pool)

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, amount, direction, person, description, raw, created_at
            FROM transactions
            WHERE user_id = $1
              AND created_at >= $2
              AND created_at < $3
            ORDER BY created_at DESC
            """,
            int(user_id),
            start_utc,
            end_utc,
        )

    out: List[Dict[str, Any]] = []
    for r in rows:
        created_at = r["created_at"]
        out.append(
            {
                "id": int(r["id"]),
                "amount": int(r["amount"]),
                "direction": r["direction"],
                "person": r["person"],
                "description": r["description"],
                "raw": r["raw"],
                "created_at": created_at.isoformat() if created_at else None,
            }
        )
    return out


async def get_recent_transactions(
    *,
    user_id: int,
    limit: int = 5,
    pool: asyncpg.Pool,
) -> List[Dict[str, Any]]:
    """
    Return the most recent transactions for a user.
    """
    await init_db(pool)
    limit = max(1, min(int(limit), 50))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, amount, direction, person, description, raw, created_at
            FROM transactions
            WHERE user_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            int(user_id),
            int(limit),
        )
    out: List[Dict[str, Any]] = []
    for r in rows:
        created_at = r["created_at"]
        out.append(
            {
                "id": int(r["id"]),
                "amount": int(r["amount"]),
                "direction": r["direction"],
                "person": r["person"],
                "description": r["description"],
                "raw": r["raw"],
                "created_at": created_at.isoformat() if created_at else None,
            }
        )
    return out


async def delete_transaction(
    *,
    user_id: int,
    tx_id: int,
    pool: asyncpg.Pool,
) -> bool:
    """
    Delete a transaction by id (scoped to user). Returns True if deleted.
    """
    await init_db(pool)
    async with pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM transactions WHERE id = $1 AND user_id = $2",
            int(tx_id),
            int(user_id),
        )
    try:
        return int(status.split()[-1]) > 0
    except Exception:
        return False


async def update_transaction(
    parsed: Dict[str, Any],
    *,
    user_id: int,
    tx_id: int,
    pool: asyncpg.Pool,
) -> bool:
    """
    Update a transaction by id (scoped to user). Returns True if updated.
    """
    await init_db(pool)

    amount = int(parsed["amount"])
    direction = parsed["direction"]
    person = parsed.get("person")
    description = parsed.get("description") or ""
    raw = parsed.get("raw") or ""

    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE transactions
            SET amount = $1, direction = $2, person = $3, description = $4, raw = $5
            WHERE id = $6 AND user_id = $7
            """,
            amount,
            direction,
            person,
            description,
            raw,
            int(tx_id),
            int(user_id),
        )
    try:
        return int(status.split()[-1]) > 0
    except Exception:
        return False


def _start_of_week(dt: datetime, week_start: int = 0) -> datetime:
    """
    week_start: 0=Monday, 6=Sunday
    """
    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (dt.weekday() - week_start) % 7
    return dt - timedelta(days=delta)


def _start_of_month(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def get_summary(
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    pool: asyncpg.Pool,
) -> Summary:
    """
    Summary in [start, end).
    Note: totals include all directions; you can display them separately.
    """
    await init_db(pool)

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*)::bigint
            FROM transactions
            WHERE user_id = $1 AND created_at >= $2 AND created_at < $3
            """,
            int(user_id),
            start_utc,
            end_utc,
        )

        rows = await conn.fetch(
            """
            SELECT direction, COALESCE(SUM(amount),0)::bigint AS total
            FROM transactions
            WHERE user_id = $1 AND created_at >= $2 AND created_at < $3
            GROUP BY direction
            """,
            int(user_id),
            start_utc,
            end_utc,
        )
        totals_by_direction = {r["direction"]: int(r["total"]) for r in rows}
        for key in ("expense", "payable", "receivable"):
            totals_by_direction.setdefault(key, 0)

        rows = await conn.fetch(
            """
            SELECT person, COALESCE(SUM(amount),0)::bigint AS total
            FROM transactions
            WHERE user_id = $1 AND created_at >= $2 AND created_at < $3
              AND person IS NOT NULL AND btrim(person) <> ''
            GROUP BY person
            ORDER BY total DESC
            """,
            int(user_id),
            start_utc,
            end_utc,
        )
        totals_by_person = {r["person"]: int(r["total"]) for r in rows}

        rows = await conn.fetch(
            """
            SELECT (created_at AT TIME ZONE 'UTC')::date AS day, COALESCE(SUM(amount),0)::bigint AS total
            FROM transactions
            WHERE user_id = $1 AND created_at >= $2 AND created_at < $3
            GROUP BY (created_at AT TIME ZONE 'UTC')::date
            ORDER BY day ASC
            """,
            int(user_id),
            start_utc,
            end_utc,
        )
        daily_totals = [(r["day"].isoformat(), int(r["total"])) for r in rows]

    return Summary(
        start=start_utc.isoformat(),
        end=end_utc.isoformat(),
        totals_by_direction=totals_by_direction,
        totals_by_person=totals_by_person,
        daily_totals=daily_totals,
        count=int(count or 0),
    )


async def get_week_summary(
    *,
    user_id: int,
    now: Optional[datetime] = None,
    week_start: int = 0,  # Monday
    pool: asyncpg.Pool,
) -> Summary:
    now = now or datetime.now(timezone.utc)
    start = _start_of_week(now, week_start=week_start)
    end = now
    return await get_summary(user_id=user_id, start=start, end=end, pool=pool)


async def get_month_summary(
    *,
    user_id: int,
    now: Optional[datetime] = None,
    pool: asyncpg.Pool,
) -> Summary:
    now = now or datetime.now(timezone.utc)
    start = _start_of_month(now)
    end = now
    return await get_summary(user_id=user_id, start=start, end=end, pool=pool)


def format_summary_text(summary: Summary) -> str:
    """
    Helpful for bot replies (plain text).
    """
    d = summary.totals_by_direction
    lines = []
    lines.append(f"Records: {summary.count}")
    lines.append(f"Expense: {d.get('expense',0)}")
    lines.append(f"You owe (payable): {d.get('payable',0)}")
    lines.append(f"Others owe you (receivable): {d.get('receivable',0)}")

    if summary.totals_by_person:
        lines.append("\nBy person:")
        for k, v in list(summary.totals_by_person.items())[:10]:
            lines.append(f"- {k}: {v}")

    if summary.daily_totals:
        lines.append("\nDaily totals:")
        for day, total in summary.daily_totals[-7:]:
            lines.append(f"- {day}: {total}")

    return "\n".join(lines)
