"""Button vote/counter pada post di channel."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from ..db import Database
from ..publisher import refresh_post
from ..richtext import vote_indexes
from ..tgapi import BotAPIError, RawAPI

log = logging.getLogger(__name__)
router = Router(name="votes")
_tasks: set[asyncio.Task] = set()


@router.callback_query(F.data == "pv")
async def preview_press(cb: CallbackQuery) -> None:
    await cb.answer("Ini hanya preview. Button vote aktif setelah diposting.", show_alert=True)


@router.callback_query(F.data.regexp(r"^v:\d+:\d+$"))
async def on_vote(cb: CallbackQuery, db: Database, api: RawAPI) -> None:
    _, pid, idx = cb.data.split(":")  # type: ignore[union-attr]
    post = await db.get_post(int(pid))
    if not post or post["status"] != "sent" or int(idx) not in vote_indexes(post["buttons"]):
        await cb.answer("Voting sudah tidak aktif", show_alert=True)
        return
    added = await db.toggle_vote(post["id"], int(idx), cb.from_user.id)
    await cb.answer("Terima kasih! ✅" if added else "Vote dibatalkan")

    async def _refresh() -> None:
        try:
            await refresh_post(api, db, post)
        except BotAPIError as e:
            log.warning("Gagal memperbarui counter post %s: %s", post["id"], e)

    task = asyncio.create_task(_refresh())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
