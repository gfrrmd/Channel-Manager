"""Kirim / edit / hapus post ke channel (rich message dengan fallback klasik)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .db import Database
from .richtext import RICH_KINDS, build_rich_message, classic_markup
from .tgapi import BotAPIError, RawAPI

log = logging.getLogger(__name__)

MEDIA_METHODS = {
    "photo": ("sendPhoto", "photo"),
    "video": ("sendVideo", "video"),
    "animation": ("sendAnimation", "animation"),
    "audio": ("sendAudio", "audio"),
    "voice": ("sendVoice", "voice"),
    "document": ("sendDocument", "document"),
}
_locks: dict[int, asyncio.Lock] = {}


def _common(options: dict | None) -> dict:
    opts = options or {}
    out: dict = {}
    if opts.get("silent"):
        out["disable_notification"] = True
    if opts.get("protect"):
        out["protect_content"] = True
    return out


async def _send_classic(api: RawAPI, chat_id: int, content: dict, rows: list, post_id, counts, common: dict) -> dict:
    markup = classic_markup(rows, post_id, counts)
    kind = content["kind"]
    text = content.get("text") or ""
    ents = content.get("entities") or None
    if kind == "text":
        return await api.call("sendMessage", chat_id=chat_id, text=text, entities=ents,
                              reply_markup=markup, **common)
    if kind in MEDIA_METHODS:
        method, field = MEDIA_METHODS[kind]
        return await api.call(method, chat_id=chat_id, **{field: content["file_id"]},
                              caption=text or None, caption_entities=ents if text else None,
                              reply_markup=markup, **common)
    return await api.call("copyMessage", chat_id=chat_id, from_chat_id=content["src_chat_id"],
                          message_id=content["src_message_id"], reply_markup=markup, **common)


async def publish(
    api: RawAPI, chat_id: int, content: dict, rows: list, *, rich_mode: str,
    options: dict | None = None, post_id: int | None = None, counts: dict | None = None,
) -> tuple[int, str]:
    """Kirim satu post. Return (message_id, mode) dengan mode 'rich' atau 'classic'."""
    common = _common(options)
    if rich_mode != "classic" and content["kind"] in RICH_KINDS:
        try:
            res = await api.call(
                "sendRichMessage", chat_id=chat_id,
                rich_message=build_rich_message(content, rows, post_id, counts), **common,
            )
            return res["message_id"], "rich"
        except BotAPIError as e:
            # Fallback hanya untuk error "request ditolak" (400/404), bukan timeout/jaringan,
            # supaya tidak terjadi posting ganda.
            if rich_mode == "rich" or e.code not in (400, 404):
                raise
            log.warning("sendRichMessage gagal (%s); fallback ke inline keyboard biasa", e)
    res = await _send_classic(api, chat_id, content, rows, post_id, counts, common)
    return res["message_id"], "classic"


async def edit_post(api: RawAPI, post: dict, counts: dict | None = None) -> None:
    content, rows = post["content"], post["buttons"]
    try:
        if post["mode"] == "rich":
            await api.call(
                "editMessageText", chat_id=post["chat_id"], message_id=post["message_id"],
                rich_message=build_rich_message(content, rows, post["id"], counts),
            )
            return
        markup = classic_markup(rows, post["id"], counts) or {"inline_keyboard": []}
        if content["kind"] == "text":
            await api.call("editMessageText", chat_id=post["chat_id"], message_id=post["message_id"],
                           text=content["text"], entities=content.get("entities") or None,
                           reply_markup=markup)
        else:
            await api.call("editMessageReplyMarkup", chat_id=post["chat_id"],
                           message_id=post["message_id"], reply_markup=markup)
    except BotAPIError as e:
        if not e.not_modified:
            raise


async def refresh_post(api: RawAPI, db: Database, post: dict) -> None:
    """Render ulang post dengan jumlah vote terbaru (serial per post)."""
    lock = _locks.setdefault(post["id"], asyncio.Lock())
    async with lock:
        await edit_post(api, post, await db.vote_counts(post["id"]))


async def deliver_post(api: RawAPI, db: Database, post: dict, rich_mode: str) -> dict:
    """Kirim post yang sudah ada di DB, lalu catat hasilnya."""
    try:
        message_id, mode = await publish(
            api, post["chat_id"], post["content"], post["buttons"], rich_mode=rich_mode,
            options=post["options"], post_id=post["id"],
        )
    except BotAPIError as e:
        await db.update_post(post["id"], status="failed", error=str(e)[:500])
        raise
    if (post["options"] or {}).get("pin"):
        try:
            await api.call("pinChatMessage", chat_id=post["chat_id"], message_id=message_id,
                           disable_notification=True)
        except BotAPIError as e:
            log.warning("Gagal pin post %s: %s", post["id"], e)
    await db.update_post(
        post["id"], status="sent", message_id=message_id, mode=mode,
        sent_at=datetime.now(timezone.utc), error=None,
    )
    return await db.get_post(post["id"])  # type: ignore[return-value]


def friendly_error(e: BotAPIError) -> str:
    d = e.description.lower()
    if "not enough rights" in d or "need administrator" in d or "have no rights" in d:
        return "Bot tidak punya izin posting. Jadikan bot admin dengan hak 'Post messages'."
    if "chat not found" in d or "kicked" in d or "bot was" in d:
        return "Channel tidak ditemukan / bot sudah dikeluarkan dari channel."
    if "caption is too long" in d:
        return "Caption terlalu panjang (maks 1024 karakter untuk mode klasik)."
    if "message to delete not found" in d or "message can't be deleted" in d:
        return "Pesan tidak bisa dihapus (sudah terhapus atau lebih dari 48 jam tanpa hak admin penuh)."
    return e.description
