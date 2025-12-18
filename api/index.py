from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from expenses_bot import build_app
from expenses_bot.config import load_dotenv, load_settings
from expenses_bot.db_url import asyncpg_pool_kwargs
from expenses_bot.db import init_db

logger = logging.getLogger("expenses-bot")


def _project_root() -> Path:
    return ROOT


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(_project_root() / ".env")
    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    db_kwargs = asyncpg_pool_kwargs(settings.database_url)
    safe_db = {
        "host": db_kwargs.get("host"),
        "port": db_kwargs.get("port"),
        "database": db_kwargs.get("database"),
        "user": db_kwargs.get("user"),
    }
    logger.info("Connecting to Postgres (%s)", safe_db)
    db_pool = await asyncpg.create_pool(
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout=settings.db_pool_timeout,
        max_inactive_connection_lifetime=settings.db_pool_max_inactive_connection_lifetime,
        **db_kwargs,
    )
    await init_db(db_pool)
    logger.info("Postgres ready")

    telegram_app = build_app(settings, db_pool=db_pool)
    await telegram_app.initialize()
    logger.info("Telegram app initialized")

    app.state.settings = settings
    app.state.db_pool = db_pool
    app.state.telegram_app = telegram_app

    try:
        yield
    finally:
        try:
            await telegram_app.shutdown()
        finally:
            await db_pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
) -> dict[str, bool]:
    settings = getattr(request.app.state, "settings", None)
    telegram_app = getattr(request.app.state, "telegram_app", None)
    if not settings or not telegram_app:
        raise HTTPException(status_code=503, detail="App not initialized")

    if settings.webhook_secret_token:
        if not x_telegram_bot_api_secret_token:
            raise HTTPException(status_code=401, detail="Missing Telegram secret token")
        if x_telegram_bot_api_secret_token != settings.webhook_secret_token:
            raise HTTPException(status_code=401, detail="Invalid Telegram secret token")

    payload: Any
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON") from e

    update = Update.de_json(payload, telegram_app.bot)

    async def _process_update_safe() -> None:
        try:
            await telegram_app.process_update(update)
        except Exception:
            logger.exception("Failed to process Telegram update")

    if settings.webhook_process_in_background:
        asyncio.create_task(_process_update_safe())
        return {"ok": True}

    await _process_update_safe()
    return {"ok": True}
