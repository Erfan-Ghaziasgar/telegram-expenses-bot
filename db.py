# db.py
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Default DB path (good for Docker too)
DEFAULT_DB_PATH = os.environ.get("DB_PATH", "./data/expenses.db")


@dataclass
class Summary:
    start: str
    end: str
    totals_by_direction: Dict[str, int]
    totals_by_person: Dict[str, int]
    daily_totals: List[Tuple[str, int]]
    count: int


def _connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Creates tables if they don't exist.
    """
    with _connect(db_path) as conn:
        # If upgrading from an older schema that had `category`, migrate it away.
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(transactions)").fetchall()
        }
        if "category" in cols:
            conn.execute("ALTER TABLE transactions RENAME TO transactions_old;")
            conn.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK(amount >= 0),
                    direction TEXT NOT NULL CHECK(direction IN ('expense','payable','receivable')),
                    person TEXT,
                    description TEXT,
                    raw TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT INTO transactions (id, user_id, amount, direction, person, description, raw, created_at)
                SELECT id, user_id, amount, direction, person, description, raw, created_at
                FROM transactions_old;
                """
            )
            conn.execute("DROP TABLE transactions_old;")

        conn.execute(
            """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL CHECK(amount >= 0),
            direction TEXT NOT NULL CHECK(direction IN ('expense','payable','receivable')),
            person TEXT,
            description TEXT,
            raw TEXT,
            created_at TEXT NOT NULL
        );
        """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_user_time ON transactions(user_id, created_at);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_user_person ON transactions(user_id, person);"
        )
        conn.commit()


def insert_transaction(
    parsed: Dict[str, Any],
    *,
    user_id: int,
    db_path: str = DEFAULT_DB_PATH,
    created_at: Optional[datetime] = None,
) -> int:
    """
    Insert one transaction. Returns inserted row id.
    """
    init_db(db_path)

    amount = int(parsed["amount"])
    direction = parsed["direction"]
    person = parsed.get("person")
    description = parsed.get("description") or ""
    raw = parsed.get("raw") or ""

    # store timestamps as ISO8601 in UTC for consistency
    now = created_at or datetime.now(timezone.utc)
    created_at_str = now.isoformat()

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO transactions
            (user_id, amount, direction, person, description, raw, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                amount,
                direction,
                person,
                description,
                raw,
                created_at_str,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_transactions(
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """
    Return raw transactions in [start, end).
    """
    init_db(db_path)

    start_s = start.astimezone(timezone.utc).isoformat()
    end_s = end.astimezone(timezone.utc).isoformat()

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, amount, direction, person, description, raw, created_at
            FROM transactions
            WHERE user_id = ?
              AND created_at >= ?
              AND created_at < ?
            ORDER BY created_at DESC
            """,
            (user_id, start_s, end_s),
        ).fetchall()

    return [dict(r) for r in rows]


def get_recent_transactions(
    *,
    user_id: int,
    limit: int = 5,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """
    Return the most recent transactions for a user.
    """
    init_db(db_path)
    limit = max(1, min(int(limit), 50))
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, amount, direction, person, description, raw, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_transaction(
    *,
    user_id: int,
    tx_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """
    Delete a transaction by id (scoped to user). Returns True if deleted.
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM transactions WHERE id = ? AND user_id = ?",
            (int(tx_id), int(user_id)),
        )
        conn.commit()
        return bool(cur.rowcount)


def update_transaction(
    parsed: Dict[str, Any],
    *,
    user_id: int,
    tx_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """
    Update a transaction by id (scoped to user). Returns True if updated.
    """
    init_db(db_path)

    amount = int(parsed["amount"])
    direction = parsed["direction"]
    person = parsed.get("person")
    description = parsed.get("description") or ""
    raw = parsed.get("raw") or ""

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE transactions
            SET amount = ?, direction = ?, person = ?, description = ?, raw = ?
            WHERE id = ? AND user_id = ?
            """,
            (amount, direction, person, description, raw, int(tx_id), int(user_id)),
        )
        conn.commit()
        return bool(cur.rowcount)


def _start_of_week(dt: datetime, week_start: int = 0) -> datetime:
    """
    week_start: 0=Monday, 6=Sunday
    """
    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (dt.weekday() - week_start) % 7
    return dt - timedelta(days=delta)


def _start_of_month(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def get_summary(
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    db_path: str = DEFAULT_DB_PATH,
) -> Summary:
    """
    Summary in [start, end).
    Note: totals include all directions; you can display them separately.
    """
    init_db(db_path)

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    start_s = start_utc.isoformat()
    end_s = end_utc.isoformat()

    with _connect(db_path) as conn:
        # Count
        count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM transactions
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            """,
            (user_id, start_s, end_s),
        ).fetchone()["c"]

        # Totals by direction
        rows = conn.execute(
            """
            SELECT direction, COALESCE(SUM(amount),0) AS total
            FROM transactions
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY direction
            """,
            (user_id, start_s, end_s),
        ).fetchall()
        totals_by_direction = {r["direction"]: int(r["total"]) for r in rows}
        for key in ("expense", "payable", "receivable"):
            totals_by_direction.setdefault(key, 0)

        # Totals by person (only when person is set)
        rows = conn.execute(
            """
            SELECT person, COALESCE(SUM(amount),0) AS total
            FROM transactions
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
              AND person IS NOT NULL AND TRIM(person) != ''
            GROUP BY person
            ORDER BY total DESC
            """,
            (user_id, start_s, end_s),
        ).fetchall()
        totals_by_person = {r["person"]: int(r["total"]) for r in rows}

        # Daily totals (all directions combined)
        # (We store ISO timestamps; substr(1,10) gets YYYY-MM-DD)
        rows = conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COALESCE(SUM(amount),0) AS total
            FROM transactions
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY substr(created_at, 1, 10)
            ORDER BY day ASC
            """,
            (user_id, start_s, end_s),
        ).fetchall()
        daily_totals = [(r["day"], int(r["total"])) for r in rows]

    return Summary(
        start=start_utc.isoformat(),
        end=end_utc.isoformat(),
        totals_by_direction=totals_by_direction,
        totals_by_person=totals_by_person,
        daily_totals=daily_totals,
        count=int(count),
    )


def get_week_summary(
    *,
    user_id: int,
    now: Optional[datetime] = None,
    week_start: int = 0,  # Monday
    db_path: str = DEFAULT_DB_PATH,
) -> Summary:
    now = now or datetime.now(timezone.utc)
    start = _start_of_week(now, week_start=week_start)
    end = now
    return get_summary(user_id=user_id, start=start, end=end, db_path=db_path)


def get_month_summary(
    *,
    user_id: int,
    now: Optional[datetime] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Summary:
    now = now or datetime.now(timezone.utc)
    start = _start_of_month(now)
    end = now
    return get_summary(user_id=user_id, start=start, end=end, db_path=db_path)


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


if __name__ == "__main__":
    # quick manual test (requires your parser.py parse_message)
    try:
        from parser import parse_message
    except Exception:
        parse_message = None

    uid = 12345
    init_db()

    if parse_message:
        tx1 = parse_message("100 تومن پول نون")
        insert_transaction(tx1, user_id=uid)

        tx2 = parse_message("220 تومن به ممد باید بدم")
        insert_transaction(tx2, user_id=uid)

        tx3 = parse_message("۱۵۰ تومن ممد باید بهم بده")
        insert_transaction(tx3, user_id=uid)

    wk = get_week_summary(user_id=uid)
    print(format_summary_text(wk))
