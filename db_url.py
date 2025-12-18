from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import parse_qsl, unquote


def asyncpg_pool_kwargs(database_url: str) -> dict[str, Any]:
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

