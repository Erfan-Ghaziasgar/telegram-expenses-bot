from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import json

_SCHEMA_LOCK = asyncio.Lock()
_SCHEMA_READY = False
_FLOW_TTL_SECONDS = 60 * 60 * 24  # 24 hours


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
                    user_tx_id BIGINT,
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
            await conn.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS user_tx_id BIGINT;")
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
                """
                CREATE TABLE IF NOT EXISTS user_counters (
                    user_id BIGINT PRIMARY KEY,
                    next_tx_id BIGINT NOT NULL
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_flows (
                    user_id BIGINT PRIMARY KEY,
                    chat_id BIGINT,
                    flow JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                """
            )

            # Backfill user-scoped ids for existing rows.
            await conn.execute(
                """
                WITH ranked AS (
                    SELECT
                        id,
                        row_number() OVER (PARTITION BY user_id ORDER BY created_at ASC, id ASC) AS rn
                    FROM transactions
                    WHERE user_tx_id IS NULL
                )
                UPDATE transactions t
                SET user_tx_id = ranked.rn
                FROM ranked
                WHERE t.id = ranked.id;
                """
            )

            # Ensure counters are initialized/up-to-date.
            await conn.execute(
                """
                INSERT INTO user_counters (user_id, next_tx_id)
                SELECT user_id, COALESCE(MAX(user_tx_id), 0) + 1
                FROM transactions
                GROUP BY user_id
                ON CONFLICT (user_id) DO UPDATE
                SET next_tx_id = GREATEST(user_counters.next_tx_id, EXCLUDED.next_tx_id);
                """
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
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_tx_user_user_tx_id
                ON transactions(user_id, user_tx_id)
                WHERE user_tx_id IS NOT NULL;
                """
            )
        _SCHEMA_READY = True


async def wipe_all_data(*, pool: asyncpg.Pool) -> None:
    """
    Delete ALL records for ALL users.

    This is irreversible. Prefer running it only in controlled environments.
    """
    await init_db(pool)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE transactions, user_counters, user_flows RESTART IDENTITY;")


async def get_user_flow(
    *,
    user_id: int,
    chat_id: int | None,
    pool: asyncpg.Pool,
) -> dict[str, Any] | None:
    await init_db(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT flow, chat_id, updated_at FROM user_flows WHERE user_id = $1",
            int(user_id),
        )
    if not row:
        return None

    saved_chat_id = row["chat_id"]
    if chat_id is not None and saved_chat_id is not None and int(saved_chat_id) != int(chat_id):
        await clear_user_flow(user_id=user_id, pool=pool)
        return None

    updated_at = row["updated_at"]
    if isinstance(updated_at, datetime):
        age = datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)
        if age.total_seconds() > _FLOW_TTL_SECONDS:
            await clear_user_flow(user_id=user_id, pool=pool)
            return None

    flow = row["flow"]
    if isinstance(flow, str):
        try:
            flow = json.loads(flow)
        except Exception:
            await clear_user_flow(user_id=user_id, pool=pool)
            return None
    return flow if isinstance(flow, dict) else None


async def set_user_flow(
    flow: dict[str, Any],
    *,
    user_id: int,
    chat_id: int | None,
    pool: asyncpg.Pool,
) -> None:
    await init_db(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_flows (user_id, chat_id, flow, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET chat_id = EXCLUDED.chat_id,
                flow = EXCLUDED.flow,
                updated_at = EXCLUDED.updated_at
            """,
            int(user_id),
            int(chat_id) if chat_id is not None else None,
            json.dumps(flow, ensure_ascii=False),
        )


async def clear_user_flow(*, user_id: int, pool: asyncpg.Pool) -> None:
    await init_db(pool)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM user_flows WHERE user_id = $1", int(user_id))


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
        try:
            async with conn.transaction():
                allocated = await conn.fetchval(
                    """
                    WITH upsert AS (
                        INSERT INTO user_counters (user_id, next_tx_id)
                        VALUES ($1, 2)
                        ON CONFLICT (user_id) DO UPDATE
                        SET next_tx_id = user_counters.next_tx_id + 1
                        RETURNING next_tx_id
                    )
                    SELECT (next_tx_id - 1) FROM upsert;
                    """,
                    int(user_id),
                )
                user_tx_id = int(allocated)
                row = await conn.fetchrow(
                    """
                    INSERT INTO transactions
                        (
                            user_id,
                            user_tx_id,
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
                        ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT DO NOTHING
                    RETURNING user_tx_id
                    """,
                    int(user_id),
                    user_tx_id,
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
                    return int(row["user_tx_id"])
                raise RuntimeError("duplicate")
        except RuntimeError as e:
            if str(e) != "duplicate":
                raise

        existing = None
        if telegram_update_id is not None:
            existing = await conn.fetchrow(
                "SELECT user_tx_id FROM transactions WHERE telegram_update_id = $1 AND user_id = $2",
                int(telegram_update_id),
                int(user_id),
            )
        if existing is None and telegram_chat_id is not None and telegram_message_id is not None:
            existing = await conn.fetchrow(
                """
                SELECT user_tx_id
                FROM transactions
                WHERE telegram_chat_id = $1 AND telegram_message_id = $2 AND user_id = $3
                """,
                int(telegram_chat_id),
                int(telegram_message_id),
                int(user_id),
            )
        if not existing:
            raise RuntimeError("Insert conflict but existing row not found")
        return int(existing["user_tx_id"])


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
            SELECT user_tx_id AS id, amount, direction, person, description, raw, created_at
            FROM transactions
            WHERE user_id = $1
              AND created_at >= $2
              AND created_at < $3
            ORDER BY created_at DESC, user_tx_id DESC
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
            SELECT user_tx_id AS id, amount, direction, person, description, created_at
            FROM transactions
            WHERE user_id = $1
            ORDER BY created_at DESC, user_tx_id DESC
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
                "created_at": created_at.isoformat() if created_at else None,
            }
        )
    return out


async def get_transaction(
    *,
    user_id: int,
    tx_id: int,
    pool: asyncpg.Pool,
) -> Dict[str, Any] | None:
    """
    Return one transaction (scoped to user).
    """
    await init_db(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_tx_id AS id, amount, direction, person, description, raw, created_at
            FROM transactions
            WHERE user_id = $1 AND user_tx_id = $2
            """,
            int(user_id),
            int(tx_id),
        )
    if not row:
        return None
    created_at = row["created_at"]
    return {
        "id": int(row["id"]),
        "amount": int(row["amount"]),
        "direction": row["direction"],
        "person": row["person"],
        "description": row["description"],
        "raw": row["raw"],
        "created_at": created_at.isoformat() if created_at else None,
    }


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
            "DELETE FROM transactions WHERE user_tx_id = $1 AND user_id = $2",
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
            WHERE user_tx_id = $6 AND user_id = $7
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
    # "Weekly" in the bot means "last 7 days" (rolling window), not week-to-date.
    # This ensures the daily breakdown always has 7 days.
    now_utc = now.astimezone(timezone.utc)
    start = (now_utc - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = now_utc
    return await get_summary(user_id=user_id, start=start, end=end, pool=pool)


async def get_month_summary(
    *,
    user_id: int,
    now: Optional[datetime] = None,
    pool: asyncpg.Pool,
) -> Summary:
    now = now or datetime.now(timezone.utc)
    # "Monthly" in the bot means "last 30 days" (rolling window), not month-to-date.
    now_utc = now.astimezone(timezone.utc)
    start = (now_utc - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = now_utc
    return await get_summary(user_id=user_id, start=start, end=end, pool=pool)


def format_summary_text(summary: Summary) -> str:
    """
    Helpful for bot replies (plain text).
    """
    return format_summary_text_pretty(summary)


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _parse_iso_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_period(summary: Summary) -> str:
    start_dt = _parse_iso_utc(summary.start)
    end_dt = _parse_iso_utc(summary.end)
    start_s = start_dt.date().isoformat()
    end_s = end_dt.date().isoformat()
    if start_s == end_s:
        return f"{start_s} (UTC)"
    return f"{start_s} → {end_s} (UTC)"


def format_summary_text_pretty(
    summary: Summary,
    *,
    title: str | None = None,
    max_people: int = 10,
    max_days: int = 7,
) -> str:
    d = summary.totals_by_direction
    expense = int(d.get("expense", 0) or 0)
    payable = int(d.get("payable", 0) or 0)
    receivable = int(d.get("receivable", 0) or 0)
    net = receivable - payable

    lines: list[str] = []
    header = title.strip() if title else "Summary"
    lines.append(f"{header} — {_fmt_period(summary)}")

    if summary.count <= 0:
        lines.append("No records in this period.")
        return "\n".join(lines)

    lines.append(f"Records: {_fmt_int(summary.count)}")
    lines.append("")
    lines.append("Totals")
    lines.append(f"- Expense: {_fmt_int(expense)}")
    lines.append(f"- You owe: {_fmt_int(payable)}")
    lines.append(f"- Owed to you: {_fmt_int(receivable)}")
    lines.append(f"- Net: {_fmt_int(net)}")

    if summary.totals_by_person:
        items = list(summary.totals_by_person.items())
        lines.append("")
        lines.append("Top people (all types)")
        shown = items[:max_people]
        for i, (person, total) in enumerate(shown, start=1):
            lines.append(f"{i}. {person}: {_fmt_int(int(total))}")
        remaining = len(items) - len(shown)
        if remaining > 0:
            lines.append(f"... and {remaining} more")

    if summary.daily_totals:
        end_date = _parse_iso_utc(summary.end).date()
        start_date = _parse_iso_utc(summary.start).date()
        totals_by_day = {day: int(total) for day, total in summary.daily_totals}
        earliest = max(start_date, end_date - timedelta(days=max_days - 1))
        span_days = (end_date - earliest).days + 1
        days = [(earliest + timedelta(days=i)).isoformat() for i in range(span_days)]

        lines.append("")
        lines.append(f"Last {len(days)} days (all types)")
        for day in days:
            lines.append(f"- {day}: {_fmt_int(totals_by_day.get(day, 0))}")

    return "\n".join(lines)
