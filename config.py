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
    db_path: str
    log_level: str


def load_settings() -> Settings:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing bot token. Set TELEGRAM_BOT_TOKEN (or BOT_TOKEN) environment variable."
        )
    allowed_user_ids = _parse_allowed_user_ids(os.environ.get("TELEGRAM_ALLOWED_USER_IDS"))
    raw_db_path = (os.environ.get("DB_PATH") or "").strip()
    db_path = raw_db_path or "./data/expenses.db"
    db_path = str(Path(db_path).expanduser())
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    return Settings(
        token=token,
        allowed_user_ids=allowed_user_ids,
        db_path=db_path,
        log_level=log_level,
    )
