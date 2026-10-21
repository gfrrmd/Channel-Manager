from __future__ import annotations

import os
from dataclasses import dataclass


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    allowed_users: frozenset[int]
    default_tz: str
    rich_mode: str  # auto | rich | classic
    scheduler_interval: int
    log_level: str


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN belum di-set")
    if not dsn:
        raise RuntimeError("DATABASE_URL belum di-set (tambahkan plugin PostgreSQL di Railway)")
    rich_mode = os.getenv("RICH_MODE", "auto").strip().lower()
    if rich_mode not in {"auto", "rich", "classic"}:
        raise RuntimeError("RICH_MODE harus auto, rich, atau classic")
    return Settings(
        bot_token=token,
        database_url=dsn,
        allowed_users=_ids(os.getenv("ALLOWED_USERS", "")),
        default_tz=os.getenv("DEFAULT_TZ", "UTC").strip() or "UTC",
        rich_mode=rich_mode,
        scheduler_interval=max(5, int(os.getenv("SCHEDULER_INTERVAL", "15"))),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
