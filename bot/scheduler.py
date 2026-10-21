"""Worker penjadwal: polling tabel posts (aman terhadap restart / redeploy)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import LinkPreviewOptions

from .config import Settings
from .db import Database
from .publisher import deliver_post, friendly_error
from .tgapi import BotAPIError, RawAPI
from .utils import esc, post_link

log = logging.getLogger(__name__)


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except TelegramAPIError:
        pass


async def _process(bot: Bot, api: RawAPI, db: Database, settings: Settings, post: dict) -> None:
    try:
        sent = await deliver_post(api, db, post, settings.rich_mode)
    except BotAPIError as e:
        log.error("Post terjadwal #%s gagal: %s", post["id"], e)
        await _notify(bot, post["owner_id"],
                      f"❌ Post terjadwal #{post['id']} ke <b>{esc(post['channel_title'] or '')}</b> gagal:\n{esc(friendly_error(e))}")
        return
    link = post_link(sent["chat_id"], sent.get("channel_username"), sent["message_id"])
    await _notify(bot, post["owner_id"],
                  f"✅ Post terjadwal #{post['id']} terkirim ke <b>{esc(post['channel_title'] or '')}</b>" + (f"\n{link}" if link else ""))


async def run_scheduler(bot: Bot, api: RawAPI, db: Database, settings: Settings, stop: asyncio.Event) -> None:
    log.info("Scheduler aktif (interval %ss)", settings.scheduler_interval)
    while not stop.is_set():
        try:
            for post in await db.claim_due_posts(10):
                await _process(bot, api, db, settings, post)
        except Exception:  # noqa: BLE001 - loop tidak boleh mati
            log.exception("Scheduler error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.scheduler_interval)
        except asyncio.TimeoutError:
            pass
