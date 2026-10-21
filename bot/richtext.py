"""Konversi konten pengguna menjadi Rich Message (Bot API 10.3) dan parser button.

Button di dalam teks = blok `buttons` (InputRichBlockButtons) berisi RichMessageButton.
Semua fungsi di sini murni (tanpa I/O) sehingga mudah diuji.
"""
from __future__ import annotations

import re
from typing import Any

STYLES = {"primary", "success", "danger"}
MAX_PER_ROW = 8
MAX_BUTTONS = 24
RICH_KINDS = {"text", "photo", "video", "animation", "audio", "document"}

_SIMPLE = {
    "bold": "bold",
    "italic": "italic",
    "underline": "underline",
    "strikethrough": "strikethrough",
    "spoiler": "spoiler",
    "code": "code",
    "pre": "code",
}

# ---------------------------------------------------------------- teks -> RichText


def _u16(text: str) -> bytes:
    return text.encode("utf-16-le")


def _slice(u: bytes, a: int, b: int) -> str:
    return u[a * 2 : b * 2].decode("utf-16-le", errors="ignore")


def _build(u: bytes, a: int, b: int, ents: list[dict]) -> Any:
    inside = [
        e for e in ents
        if e["length"] > 0 and e["offset"] >= a and e["offset"] + e["length"] <= b
    ]
    inside.sort(key=lambda e: (e["offset"], -e["length"]))
    parts: list[Any] = []
    pos = a
    for i, e in enumerate(inside):
        s, t = e["offset"], e["offset"] + e["length"]
        if s < pos:  # tumpang tindih dengan entity sebelumnya -> dilewati
            continue
        if s > pos:
            parts.append(_slice(u, pos, s))
        nested = [x for x in inside[i + 1 :] if x["offset"] >= s and x["offset"] + x["length"] <= t]
        inner = _build(u, s, t, nested)
        kind = e["type"]
        if kind in _SIMPLE:
            node: Any = {"type": _SIMPLE[kind], "text": inner}
        elif kind == "text_link" and e.get("url"):
            node = {"type": "url", "text": inner, "url": e["url"]}
        elif kind == "text_mention" and e.get("user"):
            node = {"type": "text_mention", "text": inner, "user": e["user"]}
        elif kind == "custom_emoji" and e.get("custom_emoji_id"):
            node = {
                "type": "custom_emoji",
                "custom_emoji_id": e["custom_emoji_id"],
                "alternative_text": _slice(u, s, t) or "🙂",
            }
        else:
            node = inner
        parts.append(node)
        pos = t
    if pos < b:
        parts.append(_slice(u, pos, b))
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else parts


def text_to_blocks(text: str, entities: list[dict] | None) -> list[dict]:
    """Satu baris teks -> satu blok paragraph (entity dipotong per baris)."""
    entities = entities or []
    u = _u16(text)
    blocks: list[dict] = []
    pos = 0
    for line in text.split("\n"):
        n = len(_u16(line)) // 2
        if line.strip():
            clipped = []
            for e in entities:
                s = max(e["offset"], pos)
                t = min(e["offset"] + e["length"], pos + n)
                if t > s:
                    clipped.append({**e, "offset": s, "length": t - s})
            blocks.append({"type": "paragraph", "text": _build(u, pos, pos + n, clipped)})
        pos += n + 1
    return blocks


# ---------------------------------------------------------------- parser button

_BTN = re.compile(r"^(?P<label>.+?)\s+[-–—]\s+(?P<target>\S+)(?:\s+[-–—]\s+(?P<style>\w+))?$")


def normalize_url(raw: str) -> str:
    s = raw.strip()
    if re.match(r"^(https?|tg)://\S+$", s, re.I):
        return s
    if s.startswith("@") and len(s) > 1:
        return "https://t.me/" + s[1:]
    if s.lower().startswith("t.me/"):
        return "https://" + s
    if "." in s and " " not in s:
        return "https://" + s
    raise ValueError(f"URL tidak valid: {raw}")


def parse_buttons(text: str) -> list[list[dict]]:
    rows: list[list[dict]] = []
    total = 0
    for ln, line in enumerate(text.strip().splitlines(), 1):
        if not line.strip():
            continue
        row: list[dict] = []
        for item in line.split("|"):
            item = item.strip()
            if not item:
                continue
            m = _BTN.match(item)
            if not m:
                raise ValueError(f"Baris {ln}: format harus 'Label - https://link' (atau 'Label - vote')")
            label = m["label"].strip()
            target = m["target"].strip()
            style = (m["style"] or "").lower() or None
            if style and style not in STYLES:
                raise ValueError(f"Baris {ln}: style harus salah satu dari {', '.join(sorted(STYLES))}")
            if len(label) > 64:
                raise ValueError(f"Baris {ln}: label terlalu panjang (maks 64 karakter)")
            btn: dict[str, Any] = {"text": label}
            if style:
                btn["style"] = style
            if target.lower() == "vote":
                btn["vote"] = True
            else:
                btn["url"] = normalize_url(target)
            row.append(btn)
        if len(row) > MAX_PER_ROW:
            raise ValueError(f"Baris {ln}: maksimal {MAX_PER_ROW} button per baris")
        if row:
            rows.append(row)
            total += len(row)
    if total > MAX_BUTTONS:
        raise ValueError(f"Maksimal {MAX_BUTTONS} button per post")
    if not rows:
        raise ValueError("Tidak ada button yang terbaca")
    return rows


# ---------------------------------------------------------------- render


def _button_dict(b: dict, flat: int, post_id: int | None, counts: dict[int, int] | None) -> dict:
    out: dict[str, Any] = {"text": b["text"]}
    if b.get("style") in STYLES:
        out["style"] = b["style"]
    if b.get("vote"):
        n = (counts or {}).get(flat, 0)
        if n:
            out["text"] = f'{b["text"]} · {n}'
        out["callback_data"] = f"v:{post_id}:{flat}" if post_id else "pv"
    else:
        out["url"] = b["url"]
    return out


def button_blocks(rows: list[list[dict]], post_id: int | None = None, counts: dict[int, int] | None = None) -> list[dict]:
    blocks, flat = [], 0
    for row in rows:
        btns = []
        for b in row:
            btns.append(_button_dict(b, flat, post_id, counts))
            flat += 1
        blocks.append({"type": "buttons", "buttons": btns, "align": "center"})
    return blocks


def classic_markup(rows: list[list[dict]], post_id: int | None = None, counts: dict[int, int] | None = None) -> dict | None:
    if not rows:
        return None
    kb, flat = [], 0
    for row in rows:
        line = []
        for b in row:
            line.append(_button_dict(b, flat, post_id, counts))
            flat += 1
        kb.append(line)
    return {"inline_keyboard": kb}


def build_rich_message(content: dict, rows: list[list[dict]], post_id: int | None = None, counts: dict[int, int] | None = None) -> dict:
    blocks: list[dict] = []
    kind = content["kind"]
    if kind != "text":
        blocks.append({"type": kind, kind: {"type": kind, "media": content["file_id"]}})
    if content.get("text"):
        blocks += text_to_blocks(content["text"], content.get("entities"))
    blocks += button_blocks(rows, post_id, counts)
    return {"blocks": blocks}


def vote_indexes(rows: list[list[dict]]) -> set[int]:
    out, flat = set(), 0
    for row in rows:
        for b in row:
            if b.get("vote"):
                out.add(flat)
            flat += 1
    return out
