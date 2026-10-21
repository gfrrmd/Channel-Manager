from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import escape as esc  # noqa: F401  (re-export)
from zoneinfo import ZoneInfo

STATUS_ICON = {
    "scheduled": "🗓",
    "sending": "⏳",
    "sent": "✅",
    "failed": "❌",
    "deleted": "🗑",
    "cancelled": "🚫",
}


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


_REL = re.compile(r"^\+?(\d+)\s*([mhd])$", re.I)


def parse_when(text: str, tz_name: str, now: datetime | None = None) -> datetime:
    """Kembalikan waktu UTC. Format: 30m | 2h | 1d | 18:30 | 2026-10-25 18:30 | 25/10/2026 18:30."""
    tz = get_zone(tz_name)
    now = now or datetime.now(timezone.utc)
    s = text.strip().lower()
    when: datetime | None = None
    m = _REL.match(s)
    if m:
        n, unit = int(m[1]), m[2]
        when = now + {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
    elif re.fullmatch(r"\d{1,2}[:.]\d{2}", s):
        h, mi = (int(x) for x in re.split(r"[:.]", s))
        local = now.astimezone(tz)
        cand = local.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand <= local:
            cand += timedelta(days=1)
        when = cand
    else:
        for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
            try:
                when = datetime.strptime(s, fmt).replace(tzinfo=tz)
                break
            except ValueError:
                continue
    if when is None:
        raise ValueError("format waktu tidak dikenali")
    when = when.astimezone(timezone.utc)
    if when < now + timedelta(seconds=30):
        raise ValueError("waktu sudah lewat")
    return when


def fmt_dt(dt: datetime | None, tz_name: str) -> str:
    if dt is None:
        return "-"
    return dt.astimezone(get_zone(tz_name)).strftime("%d %b %Y %H:%M") + f" ({tz_name})"


def post_link(chat_id: int, username: str | None, message_id: int | None) -> str | None:
    if not message_id:
        return None
    if username:
        return f"https://t.me/{username}/{message_id}"
    s = str(chat_id)
    if s.startswith("-100"):
        return f"https://t.me/c/{s[4:]}/{message_id}"
    return None


def short(text: str, n: int = 28) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"
