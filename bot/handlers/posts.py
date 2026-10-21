"""Riwayat, jadwal, edit button, dan hapus post."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import Settings
from ..db import Database
from ..publisher import friendly_error, refresh_post
from ..richtext import parse_buttons
from ..tgapi import BotAPIError, RawAPI
from ..ui import respond
from ..utils import STATUS_ICON, esc, fmt_dt, post_link, short
from .compose import BUTTON_HELP, _not_command

log = logging.getLogger(__name__)
router = Router(name="posts")


class EditPost(StatesGroup):
    buttons = State()


async def show_list(event: Message | CallbackQuery, db: Database, scheduled: bool) -> None:
    statuses = ["scheduled"] if scheduled else None
    posts = await db.list_posts(event.from_user.id, statuses, 10)  # type: ignore[union-attr]
    b = InlineKeyboardBuilder()
    for p in posts:
        label = short((p["content"] or {}).get("text") or f"[{p['content']['kind']}]", 22)
        b.button(text=f"{STATUS_ICON.get(p['status'], '•')} #{p['id']} · {p['channel_title']} · {label}",
                 callback_data=f"po:{p['id']}")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(1)
    title = "🗓 <b>Post terjadwal</b>" if scheduled else "📋 <b>10 post terakhir</b>"
    await respond(event, title if posts else "Belum ada data.", b.as_markup())


@router.message(Command("scheduled"))
async def scheduled_cmd(message: Message, db: Database) -> None:
    await show_list(message, db, True)


@router.message(Command("posts"))
async def posts_cmd(message: Message, db: Database) -> None:
    await show_list(message, db, False)


@router.callback_query(F.data == "sch")
async def scheduled_cb(cb: CallbackQuery, db: Database) -> None:
    await show_list(cb, db, True)


@router.callback_query(F.data == "hist")
async def hist_cb(cb: CallbackQuery, db: Database) -> None:
    await show_list(cb, db, False)


async def _own_post(cb: CallbackQuery, db: Database, post_id: int) -> dict | None:
    post = await db.get_post(post_id)
    if not post or post["owner_id"] != cb.from_user.id:
        await cb.answer("Post tidak ditemukan", show_alert=True)
        return None
    return post


@router.callback_query(F.data.regexp(r"^po:\d+$"))
async def post_detail(cb: CallbackQuery, db: Database, settings: Settings) -> None:
    post = await _own_post(cb, db, int(cb.data.split(":")[1]))  # type: ignore[union-attr]
    if not post:
        return
    tz = await db.get_tz(cb.from_user.id, settings.default_tz)
    n_btn = sum(len(r) for r in post["buttons"])
    counts = await db.vote_counts(post["id"])
    lines = [
        f"{STATUS_ICON.get(post['status'], '•')} <b>Post #{post['id']}</b> · {post['status']}",
        f"📣 {esc(post['channel_title'] or '')}",
        f"🔘 {n_btn} button · mode {post['mode']}",
    ]
    if post["run_at"] and post["status"] == "scheduled":
        lines.append(f"🗓 {fmt_dt(post['run_at'], tz)}")
    if post["sent_at"]:
        lines.append(f"📤 {fmt_dt(post['sent_at'], tz)}")
    if counts:
        lines.append(f"🗳 Total vote: {sum(counts.values())}")
    link = post_link(post["chat_id"], post.get("channel_username"), post["message_id"])
    if link and post["status"] == "sent":
        lines.append(link)
    if post["error"]:
        lines.append(f"⚠️ {esc(post['error'][:200])}")
    b = InlineKeyboardBuilder()
    if post["status"] in ("sent", "scheduled"):
        b.button(text="✏️ Edit button", callback_data=f"pe:{post['id']}")
    if post["status"] == "sent":
        b.button(text="🗑 Hapus dari channel", callback_data=f"pdq:{post['id']}")
    if post["status"] == "scheduled":
        b.button(text="🚫 Batalkan jadwal", callback_data=f"pcx:{post['id']}")
    b.button(text="⬅️ Riwayat", callback_data="hist")
    b.adjust(1)
    await respond(cb, "\n".join(lines), b.as_markup())


# ---------------------------------------------------------------- edit button
@router.callback_query(F.data.regexp(r"^pe:\d+$"))
async def edit_buttons_ask(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    post_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    if not await _own_post(cb, db, post_id):
        return
    await cb.answer()
    await state.set_state(EditPost.buttons)
    await state.update_data(edit_post_id=post_id)
    await cb.message.answer(BUTTON_HELP)  # type: ignore[union-attr]


@router.message(EditPost.buttons, F.text, _not_command)
async def edit_buttons_input(message: Message, state: FSMContext, db: Database, api: RawAPI) -> None:
    data = await state.get_data()
    post = await db.get_post(data.get("edit_post_id", 0))
    if not post or post["owner_id"] != message.from_user.id:  # type: ignore[union-attr]
        await state.clear()
        await message.answer("Post tidak ditemukan.")
        return
    txt = message.text.strip()  # type: ignore[union-attr]
    try:
        rows = [] if txt.lower() in {"clear", "hapus"} else parse_buttons(txt)
    except ValueError as e:
        await message.answer(f"⚠️ {esc(str(e))}\n\nCoba lagi, atau /cancel.")
        return
    await db.update_post(post["id"], buttons=rows)
    post["buttons"] = rows
    await state.clear()
    if post["status"] == "sent":
        try:
            await refresh_post(api, db, post)
        except BotAPIError as e:
            await message.answer(f"❌ Button tersimpan, tapi gagal mengedit post di channel: {esc(friendly_error(e))}")
            return
        await message.answer(f"✅ Button post #{post['id']} diperbarui di channel.")
    else:
        await message.answer(f"✅ Button post #{post['id']} diperbarui (akan dipakai saat dijadwalkan terkirim).")


# ---------------------------------------------------------------- hapus / batalkan
@router.callback_query(F.data.regexp(r"^pdq:\d+$"))
async def delete_confirm(cb: CallbackQuery, db: Database) -> None:
    post_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    if not await _own_post(cb, db, post_id):
        return
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Ya, hapus", callback_data=f"pd:{post_id}")
    b.button(text="Batal", callback_data=f"po:{post_id}")
    b.adjust(2)
    await respond(cb, f"Hapus post #{post_id} dari channel? Tindakan ini tidak bisa dibatalkan.", b.as_markup())


@router.callback_query(F.data.regexp(r"^pd:\d+$"))
async def delete_post(cb: CallbackQuery, bot: Bot, db: Database) -> None:
    post_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    post = await _own_post(cb, db, post_id)
    if not post:
        return
    try:
        await bot.delete_message(post["chat_id"], post["message_id"])
    except TelegramAPIError as e:
        await cb.answer()
        await cb.message.answer(f"❌ Gagal menghapus: {esc(friendly_error(BotAPIError('deleteMessage', 400, str(e))))}")  # type: ignore[union-attr]
        return
    await db.update_post(post_id, status="deleted")
    await cb.answer("Terhapus")
    await show_list(cb, db, False)


@router.callback_query(F.data.regexp(r"^pcx:\d+$"))
async def cancel_schedule(cb: CallbackQuery, db: Database) -> None:
    post_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    post = await _own_post(cb, db, post_id)
    if not post:
        return
    ok = await db.cancel_if_scheduled(post_id)
    await cb.answer("Jadwal dibatalkan" if ok else "Post sudah diproses", show_alert=not ok)
    await show_list(cb, db, True)
