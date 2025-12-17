from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path | str | None = None) -> None:
    """
    Minimal `.env` loader (no dependency). Existing environment variables win.
    Supports:
      - comments (# ...)
      - optional `export KEY=...`
      - quoted values: KEY="..." or KEY='...'
    """
    env_path = Path(path) if path is not None else Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
            value = value[1:-1]

        os.environ[key] = value


def _parse_allowed_user_ids(raw: str | None) -> set[int] | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.add(int(part))
    return ids or None


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_user_ids: set[int] | None
    database_url: str
    webhook_secret_token: str | None
    log_level: str
    db_pool_min_size: int
    db_pool_max_size: int
    db_pool_timeout: float
    db_pool_max_inactive_connection_lifetime: float
    webhook_process_in_background: bool


def _parse_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(raw: str | None, *, default: int, min_value: int | None = None) -> int:
    try:
        value = int((raw or "").strip())
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(min_value, value)
    return value


def _parse_float(raw: str | None, *, default: float, min_value: float | None = None) -> float:
    try:
        value = float((raw or "").strip())
    except Exception:
        value = float(default)
    if min_value is not None:
        value = max(min_value, value)
    return value


def load_settings() -> Settings:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing bot token. Set TELEGRAM_BOT_TOKEN (or BOT_TOKEN) environment variable."
        )
    allowed_user_ids = _parse_allowed_user_ids(os.environ.get("TELEGRAM_ALLOWED_USER_IDS"))
    database_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("SUPABASE_DATABASE_URL")
        or os.environ.get("SUPABASE_DB_URL")
        or ""
    ).strip()
    if not database_url:
        raise RuntimeError(
            "Missing database URL. Set DATABASE_URL (Supabase Postgres connection string)."
        )
    if database_url.startswith(("http://", "https://")):
        raise RuntimeError(
            "Invalid DATABASE_URL. Use the Supabase Postgres connection string "
            "(postgresql://...), not the Supabase Project URL (https://...)."
        )
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://") :]
    if not database_url.startswith("postgresql://"):
        raise RuntimeError(
            "Invalid DATABASE_URL. Expected a Postgres connection string starting with "
            "postgresql:// (or postgres://)."
        )
    webhook_secret_token = (os.environ.get("TELEGRAM_WEBHOOK_SECRET_TOKEN") or "").strip() or None
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    db_pool_min_size = _parse_int(os.environ.get("DB_POOL_MIN_SIZE"), default=1, min_value=1)
    db_pool_max_size = _parse_int(os.environ.get("DB_POOL_MAX_SIZE"), default=5, min_value=1)
    if db_pool_max_size < db_pool_min_size:
        db_pool_max_size = db_pool_min_size
    db_pool_timeout = _parse_float(os.environ.get("DB_POOL_TIMEOUT"), default=20.0, min_value=1.0)
    db_pool_max_inactive_connection_lifetime = _parse_float(
        os.environ.get("DB_POOL_MAX_INACTIVE_SECS"),
        default=60.0,
        min_value=0.0,
    )
    webhook_process_in_background = _parse_bool(os.environ.get("TELEGRAM_WEBHOOK_BACKGROUND"))

    return Settings(
        token=token,
        allowed_user_ids=allowed_user_ids,
        database_url=database_url,
        webhook_secret_token=webhook_secret_token,
        log_level=log_level,
        db_pool_min_size=db_pool_min_size,
        db_pool_max_size=db_pool_max_size,
        db_pool_timeout=db_pool_timeout,
        db_pool_max_inactive_connection_lifetime=db_pool_max_inactive_connection_lifetime,
        webhook_process_in_background=webhook_process_in_background,
    )
