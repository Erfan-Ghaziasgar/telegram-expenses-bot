from __future__ import annotations

import asyncio
import logging
import ssl
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote

import asyncpg
from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import build_app
from config import load_dotenv, load_settings
from db import init_db

logger = logging.getLogger("expenses-bot")


def _project_root() -> Path:
    return ROOT


def _asyncpg_pool_kwargs(database_url: str) -> dict[str, Any]:
    """
    Accepts Supabase Postgres connection strings and returns kwargs for asyncpg.create_pool.

    Notes:
    - Supabase connection strings often include `?sslmode=require` (libpq-style).
      asyncpg doesn't understand `sslmode`, so we translate it to `ssl=...`.
    - Passwords may contain URL-reserved characters. We parse the URL manually so users don't
      have to URL-encode passwords.
    """
    url = database_url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if not url.startswith("postgresql://"):
        raise ValueError("DATABASE_URL must start with postgresql:// (or postgres://)")

    rest = url[len("postgresql://") :]
    rest, _, _fragment = rest.partition("#")

    if "@" in rest:
        creds, host_and_path = rest.rsplit("@", 1)
        if ":" in creds:
            user, password = creds.split(":", 1)
        else:
            user, password = creds, None
    else:
        user, password = None, None
        host_and_path = rest

    hostport, has_slash, path_and_query = host_and_path.partition("/")
    if not has_slash:
        raise ValueError("DATABASE_URL is missing the database name (expected .../postgres)")

    database, _, query_str = path_and_query.partition("?")
    if not database:
        raise ValueError("DATABASE_URL is missing the database name (expected .../postgres)")

    query = dict(parse_qsl(query_str, keep_blank_values=True))
    sslmode = (query.pop("sslmode", "") or "").lower()
    pgbouncer = (query.pop("pgbouncer", "") or "").lower() in {"1", "true", "yes", "on"}

    host = hostport
    port: int | None = None
    if hostport.startswith("["):
        end = hostport.find("]")
        if end == -1:
            raise ValueError("Invalid DATABASE_URL (malformed IPv6 host)")
        host = hostport[1:end]
        rest_hp = hostport[end + 1 :]
        if rest_hp.startswith(":"):
            port_str = rest_hp[1:]
            if port_str:
                port = int(port_str)
    else:
        if ":" in hostport:
            host, port_str = hostport.rsplit(":", 1)
            if port_str:
                port = int(port_str)

    if not host:
        raise ValueError("DATABASE_URL is missing a host")

    kwargs: dict[str, Any] = {"host": host, "database": database}
    if user:
        kwargs["user"] = user
    if password is not None:
        kwargs["password"] = unquote(password)
    if port is not None:
        kwargs["port"] = port

    if sslmode in {"require", "verify-ca", "verify-full"}:
        ctx = ssl.create_default_context()
        if sslmode == "require":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        elif sslmode == "verify-ca":
            ctx.check_hostname = False
        kwargs["ssl"] = ctx
    elif sslmode == "disable":
        kwargs["ssl"] = False
    elif host.endswith(".supabase.co") or host.endswith(".supabase.com"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ctx

    if pgbouncer or port == 6543 or "pooler.supabase" in host:
        kwargs["statement_cache_size"] = 0
        kwargs["max_cached_statement_lifetime"] = 0
    return kwargs


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

    db_kwargs = _asyncpg_pool_kwargs(settings.database_url)
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
