from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from expenses_bot.config import load_dotenv, load_settings
from expenses_bot.db import wipe_all_data
from expenses_bot.db_url import asyncpg_pool_kwargs

async def _reset_db() -> None:
    load_dotenv(".env")
    settings = load_settings()
    db_kwargs = asyncpg_pool_kwargs(settings.database_url)
    safe_db = {
        "host": db_kwargs.get("host"),
        "port": db_kwargs.get("port"),
        "database": db_kwargs.get("database"),
        "user": db_kwargs.get("user"),
    }
    print(f"Resetting ALL data in Postgres: {safe_db}")
    pool = await asyncpg.create_pool(min_size=1, max_size=1, timeout=20, **db_kwargs)
    try:
        await wipe_all_data(pool=pool)
    finally:
        await pool.close()
    print("OK: database reset completed (all records deleted).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete ALL records for ALL users.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually perform the reset (required).",
    )
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to reset without --yes")
    asyncio.run(_reset_db())


if __name__ == "__main__":
    main()
