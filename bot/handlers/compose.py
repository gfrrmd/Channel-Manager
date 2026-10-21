"""Alur utama: kirim konten -> (opsional) tambah button -> preview -> post / jadwalkan."""
from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.enums import ChatType, ContentType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import Settings
from ..db import Database
from ..publisher import deliver_post, friendly_error, publish
from ..richtext import parse_buttons
from ..tgapi import BotAPIError, RawAPI
from ..ui import main_menu_kb
from ..utils import esc, fmt_dt, parse_when, post_link
from .channels import add_channel_inline, verify_channel

log = logging.getLogger(__name__)
router = Router(name="compose")


class Compose(StatesGroup):
    content = State()
    buttons = State()
    schedule = State()


CONTENT_TYPES = {
    ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION,
    ContentType.AUDIO, ContentType.VOICE, ContentType.DOCUMENT, ContentType.STICKER,
    ContentType.VIDEO_NOTE, ContentType.POLL, ContentType.LOCATION, ContentType.CONTACT,
    ContentType.VENUE, ContentType.DICE,
}

BUTTON_HELP = (
    "🔘 <b>Kirim daftar button</b>\n"
    "Satu baris = satu baris button. Pisahkan beberapa button dalam satu baris dengan <code>|</code>.\n\n"
    "<code>Website - https://example.com\n"
    "Chat - @username | Docs - docs.example.com\n"
    "Beli - https://shop.id - primary\n"
    "👍 Suka - vote</code>\n\n"
    "• Style opsional di akhir: <code>primary</code>, <code>success</code>, <code>danger</code>\n"
    "• <code>vote</code> = button counter (jumlah klik tampil di label)\n"
    "• Ketik <code>clear</code> untuk menghapus semua button\n"
    "• Batal: /cancel"
)


def _not_command(m: Message) -> bool:
    return not (m.text or "").startswith("/")


def extract_content(m: Message) -> dict:
    ents = m.entities or m.caption_entities or []
    d: dict = {
        "text": m.text or m.caption or "",
        "entities": [e.model_dump(mode="json", exclude_none=True) for e in ents],
        "src_chat_id": m.chat.id,
        "src_message_id": m.message_id,
        "file_id": None,
    }
    if m.text:
        d["kind"] = "text"
    elif m.photo:
        d["kind"], d["file_id"] = "photo", m.photo[-1].file_id
    elif m.video:
        d["kind"], d["file_id"] = "video", m.video.file_id
    elif m.animation:  # harus sebelum document (animation juga mengisi document)
        d["kind"], d["file_id"] = "animation", m.animation.file_id
    elif m.audio:
        d["kind"], d["file_id"] = "audio", m.audio.file_id
    elif m.voice:
        d["kind"], d["file_id"] = "voice", m.voice.file_id
    elif m.document:
        d["kind"], d["file_id"] = "document", m.document.file_id
    else:
        d["kind"] = "other"
    return d


# ---------------------------------------------------------------- UI draft
def menu_text(d: dict) -> str:
    on = lambda v: "ON" if v else "off"  # noqa: E731
    n = sum(len(r) for r in d["rows"])
    return (
        f"📣 Target: <b>{esc(d['chat_title'])}</b>\n"
        f"🔘 Button: {n}\n"
        f"🔕 Silent: {on(d['silent'])} · 🔒 Protect: {on(d['protect'])} · 📌 Pin: {on(d['pin'])}\n\n"
        "Pilih aksi:"
    )


def menu_kb(d: dict) -> InlineKeyboardMarkup:
    mark = lambda v: "✓" if v else "✗"  # noqa: E731
    b = InlineKeyboardBuilder()
    b.button(text="🚀 Post sekarang", callback_data="pm:now")
    b.button(text="🗓 Jadwalkan", callback_data="pm:sched")
    b.button(text="🔘 Edit button", callback_data="pm:btn")
    b.button(text="📣 Ganti channel", callback_data="pm:chan")
    b.button(text=f"🔕 Silent {mark(d['silent'])}", callback_data="pm:silent")
    b.button(text=f"🔒 Protect {mark(d['protect'])}", callback_data="pm:protect")
    b.button(text=f"📌 Pin {mark(d['pin'])}", callback_data="pm:pin")
    b.button(text="❌ Batal", callback_data="cancel")
    b.adjust(2, 2, 3, 1)
    return b.as_markup()


async def get_draft(state: FSMContext) -> dict | None:
    return (await state.get_data()).get("draft")


async def send_preview(bot: Bot, api: RawAPI, settings: Settings, user_id: int, state: FSMContext) -> None:
    d = await get_draft(state)
    if not d:
        return
    for key in ("preview_id", "menu_id"):
        if d.get(key):
            with suppress(TelegramAPIError):
                await bot.delete_message(user_id, d[key])
            d[key] = None
    try:
        msg_id, mode = await publish(api, user_id, d["content"], d["rows"], rich_mode=settings.rich_mode)
    except BotAPIError as e:
        await bot.send_message(user_id, f"❌ Gagal membuat preview: {esc(friendly_error(e))}")
        return
    d["preview_id"] = msg_id
    if d["content"]["kind"] == "other" and d["rows"]:
        note = "\nℹ️ Tipe pesan ini tidak mendukung button di dalam teks; button tampil di bawah pesan."
    else:
        note = ""
    menu = await bot.send_message(user_id, menu_text(d) + note, reply_markup=menu_kb(d))
    d["menu_id"] = menu.message_id
    d["preview_mode"] = mode
    await state.update_data(draft=d)


async def _expired(cb: CallbackQuery) -> None:
    await cb.answer("Sesi kedaluwarsa. Kirim ulang konten.", show_alert=True)


# ---------------------------------------------------------------- mulai
@router.message(Command("post"))
async def post_cmd(message: Message, state: FSMContext) -> None:
    await state.set_state(Compose.content)
    await message.answer("✍️ Kirim konten yang mau dipost (teks, foto, video, file, dll).")


@router.callback_query(F.data == "np")
async def new_post_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Compose.content)
    await cb.message.answer("✍️ Kirim konten yang mau dipost (teks, foto, video, file, dll).")  # type: ignore[union-attr]


@router.message(
    StateFilter(None, Compose.content),
    F.chat.type == ChatType.PRIVATE,
    F.content_type.in_(CONTENT_TYPES),
    _not_command,
)
async def on_content(message: Message, state: FSMContext, db: Database) -> None:
    uid = message.from_user.id  # type: ignore[union-attr]
    chans = await db.list_channels(uid)
    if not chans:
        await message.answer(
            "Kamu belum punya channel terhubung. Hubungkan dulu, lalu kirim ulang kontennya.",
            reply_markup=add_channel_inline(),
        )
        return
    ch = chans[0]
    draft = {
        "content": extract_content(message),
        "rows": [],
        "chat_id": ch["chat_id"],
        "chat_title": ch["title"],
        "chat_username": ch.get("username"),
        "silent": False, "protect": False, "pin": False,
        "preview_id": None, "menu_id": None,
    }
    await state.set_state(None)
    await state.update_data(draft=draft)
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ya, tambah button", callback_data="btn:yes")
    b.button(text="⏭ Lewati", callback_data="btn:no")
    b.button(text="❌ Batal", callback_data="cancel")
    b.adjust(2, 1)
    await message.reply("Mau menambahkan <b>button di dalam teks</b>?", reply_markup=b.as_markup())


@router.callback_query(F.data == "btn:no")
async def buttons_skip(cb: CallbackQuery, state: FSMContext, bot: Bot, api: RawAPI, settings: Settings) -> None:
    if not await get_draft(state):
        return await _expired(cb)
    await cb.answer()
    with suppress(TelegramAPIError):
        await cb.message.delete()  # type: ignore[union-attr]
    await send_preview(bot, api, settings, cb.from_user.id, state)


@router.callback_query(F.data.in_({"btn:yes", "pm:btn"}))
async def buttons_ask(cb: CallbackQuery, state: FSMContext) -> None:
    if not await get_draft(state):
        return await _expired(cb)
    await cb.answer()
    await state.set_state(Compose.buttons)
    await cb.message.answer(BUTTON_HELP)  # type: ignore[union-attr]


@router.message(Compose.buttons, F.text, _not_command)
async def buttons_input(message: Message, state: FSMContext, bot: Bot, api: RawAPI, settings: Settings) -> None:
    d = await get_draft(state)
    if not d:
        await state.clear()
        await message.answer("Sesi kedaluwarsa. Kirim ulang konten.")
        return
    txt = message.text.strip()  # type: ignore[union-attr]
    if txt.lower() in {"clear", "hapus"}:
        rows: list = []
    else:
        try:
            rows = parse_buttons(txt)
        except ValueError as e:
            await message.answer(f"⚠️ {esc(str(e))}\n\nCoba lagi, atau /cancel.")
            return
    d["rows"] = rows
    await state.update_data(draft=d)
    await state.set_state(None)
    await send_preview(bot, api, settings, message.from_user.id, state)  # type: ignore[union-attr]


# ---------------------------------------------------------------- menu preview
@router.callback_query(F.data.in_({"pm:silent", "pm:protect", "pm:pin"}))
async def toggle(cb: CallbackQuery, state: FSMContext) -> None:
    d = await get_draft(state)
    if not d:
        return await _expired(cb)
    key = cb.data.split(":")[1]  # type: ignore[union-attr]
    d[key] = not d[key]
    await state.update_data(draft=d)
    await cb.answer()
    await cb.message.edit_text(menu_text(d), reply_markup=menu_kb(d))  # type: ignore[union-attr]


@router.callback_query(F.data == "pm:chan")
async def choose_channel(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    d = await get_draft(state)
    if not d:
        return await _expired(cb)
    b = InlineKeyboardBuilder()
    for c in await db.list_channels(cb.from_user.id):
        mark = "● " if c["chat_id"] == d["chat_id"] else ""
        b.button(text=f"{mark}{c['title']}", callback_data=f"pc:{c['chat_id']}")
    b.button(text="⬅️ Kembali", callback_data="pm:back")
    b.adjust(1)
    await cb.answer()
    await cb.message.edit_text("Pilih channel tujuan:", reply_markup=b.as_markup())  # type: ignore[union-attr]


@router.callback_query(F.data.regexp(r"^pc:-?\d+$"))
async def set_channel(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    d = await get_draft(state)
    if not d:
        return await _expired(cb)
    ch = await db.get_channel(cb.from_user.id, int(cb.data.split(":")[1]))  # type: ignore[union-attr]
    if ch:
        d.update(chat_id=ch["chat_id"], chat_title=ch["title"], chat_username=ch.get("username"))
        await state.update_data(draft=d)
    await cb.answer()
    await cb.message.edit_text(menu_text(d), reply_markup=menu_kb(d))  # type: ignore[union-attr]


@router.callback_query(F.data == "pm:back")
async def back_to_menu(cb: CallbackQuery, state: FSMContext) -> None:
    d = await get_draft(state)
    if not d:
        return await _expired(cb)
    await cb.answer()
    await cb.message.edit_text(menu_text(d), reply_markup=menu_kb(d))  # type: ignore[union-attr]


@router.callback_query(F.data == "cancel")
async def cancel_cb(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    d = await get_draft(state)
    await state.clear()
    await cb.answer("Dibatalkan")
    if d and d.get("preview_id"):
        with suppress(TelegramAPIError):
            await bot.delete_message(cb.from_user.id, d["preview_id"])
    with suppress(TelegramAPIError):
        await cb.message.edit_text("Dibatalkan.", reply_markup=main_menu_kb())  # type: ignore[union-attr]


# ---------------------------------------------------------------- posting
def _channel(d: dict) -> dict:
    return {"chat_id": d["chat_id"], "title": d["chat_title"], "username": d.get("chat_username")}


def _options(d: dict) -> dict:
    return {"silent": d["silent"], "protect": d["protect"], "pin": d["pin"]}


@router.callback_query(F.data == "pm:now")
async def post_now(cb: CallbackQuery, state: FSMContext, bot: Bot, api: RawAPI, db: Database, settings: Settings) -> None:
    d = await get_draft(state)
    if not d:
        return await _expired(cb)
    await cb.answer("Memposting…")
    _, problems, _w = await verify_channel(bot, d["chat_id"], cb.from_user.id)
    if problems:
        await cb.message.answer("⚠️ " + "\n⚠️ ".join(problems))  # type: ignore[union-attr]
        return
    post_id = await db.create_post(
        owner_id=cb.from_user.id, channel=_channel(d), content=d["content"], buttons=d["rows"],
        options=_options(d), mode="rich" if settings.rich_mode != "classic" else "classic",
        status="sending",
    )
    post = await db.get_post(post_id)
    try:
        post = await deliver_post(api, db, post, settings.rich_mode)  # type: ignore[arg-type]
    except BotAPIError as e:
        await cb.message.answer(f"❌ Gagal memposting: {esc(friendly_error(e))}")  # type: ignore[union-attr]
        return
    link = post_link(post["chat_id"], post.get("channel_username"), post["message_id"])
    text = f"✅ Terkirim ke <b>{esc(d['chat_title'])}</b> (post #{post_id}, mode {post['mode']})"
    if link:
        text += f"\n{link}"
    await state.update_data(draft=None)
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Kelola post", callback_data=f"po:{post_id}")
    b.button(text="✍️ Post baru", callback_data="np")
    b.adjust(2)
    await cb.message.edit_text(text, reply_markup=b.as_markup(),
                                    link_preview_options=LinkPreviewOptions(is_disabled=True))  # type: ignore[union-attr]


@router.callback_query(F.data == "pm:sched")
async def schedule_ask(cb: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await get_draft(state):
        return await _expired(cb)
    await cb.answer()
    tz = await db.get_tz(cb.from_user.id, settings.default_tz)
    await state.set_state(Compose.schedule)
    await cb.message.answer(  # type: ignore[union-attr]
        "🗓 <b>Kapan diposting?</b>\n"
        "<code>30m</code> · <code>2h</code> · <code>1d</code> (dari sekarang)\n"
        "<code>18:30</code> (hari ini/besok)\n"
        "<code>2026-10-25 18:30</code>\n\n"
        f"Zona waktu: <b>{esc(tz)}</b> (ubah dengan /tz). Batal: /cancel"
    )


@router.message(Compose.schedule, F.text, _not_command)
async def schedule_input(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    uid = message.from_user.id  # type: ignore[union-attr]
    d = await get_draft(state)
    if not d:
        await state.clear()
        await message.answer("Sesi kedaluwarsa. Kirim ulang konten.")
        return
    tz = await db.get_tz(uid, settings.default_tz)
    try:
        when = parse_when(message.text, tz)  # type: ignore[arg-type]
    except ValueError as e:
        await message.answer(f"⚠️ {esc(str(e))}. Coba lagi, contoh: <code>2h</code> atau <code>18:30</code>.")
        return
    post_id = await db.create_post(
        owner_id=uid, channel=_channel(d), content=d["content"], buttons=d["rows"],
        options=_options(d), mode="rich" if settings.rich_mode != "classic" else "classic",
        status="scheduled", run_at=when,
    )
    await state.set_state(None)
    await state.update_data(draft=None)
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Kelola post", callback_data=f"po:{post_id}")
    b.button(text="🗓 Terjadwal", callback_data="sch")
    await message.answer(
        f"🗓 Dijadwalkan: post #{post_id} ke <b>{esc(d['chat_title'])}</b>\n{fmt_dt(when, tz)}",
        reply_markup=b.as_markup(),
    )


@router.message(StateFilter(Compose.buttons, Compose.schedule), _not_command)
async def expect_text(message: Message) -> None:
    await message.answer("Kirim sebagai teks ya. Batal: /cancel")
