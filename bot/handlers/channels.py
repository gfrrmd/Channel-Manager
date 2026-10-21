from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Chat, ChatMemberUpdated, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import Settings
from ..db import Database
from ..tgapi import RawAPI
from ..ui import back_kb, main_menu_kb, respond
from ..utils import esc

log = logging.getLogger(__name__)
router = Router(name="channels")

REQUEST_ID = 1


class AddChannel(StatesGroup):
    waiting = State()


async def verify_channel(bot: Bot, chat_id: int | str, user_id: int) -> tuple[Chat | None, list[str], list[str]]:
    """Return (chat, masalah_fatal, peringatan)."""
    problems: list[str] = []
    warnings: list[str] = []
    try:
        chat = await bot.get_chat(chat_id)
    except TelegramAPIError:
        return None, ["Bot belum bisa mengakses channel itu. Tambahkan bot sebagai admin dulu."], []
    if chat.type != "channel":
        problems.append("Chat ini bukan channel.")
        return chat, problems, warnings
    me = await bot.me()
    try:
        bm = await bot.get_chat_member(chat.id, me.id)
    except TelegramAPIError:
        return chat, ["Bot bukan anggota channel ini."], []
    if bm.status != "administrator":
        problems.append("Bot belum menjadi admin channel.")
    else:
        if not getattr(bm, "can_post_messages", False):
            problems.append("Bot tidak punya hak <b>Post messages</b>.")
        if not getattr(bm, "can_edit_messages", False):
            warnings.append("Tanpa hak <b>Edit messages</b>, edit button & vote counter tidak jalan.")
        if not getattr(bm, "can_delete_messages", False):
            warnings.append("Tanpa hak <b>Delete messages</b>, fitur hapus post tidak jalan.")
    try:
        um = await bot.get_chat_member(chat.id, user_id)
        if um.status not in ("creator", "administrator"):
            problems.append("Kamu bukan admin channel ini.")
    except TelegramAPIError:
        problems.append("Tidak bisa memverifikasi status admin kamu di channel ini.")
    return chat, problems, warnings


def add_channel_inline() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Tambah channel", callback_data="ch:add")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(1)
    return b.as_markup()


async def show_channels(event: Message | CallbackQuery, db: Database) -> None:
    chans = await db.list_channels(event.from_user.id)  # type: ignore[union-attr]
    b = InlineKeyboardBuilder()
    for c in chans:
        b.button(text=f"📣 {c['title']}", callback_data=f"ch:{c['chat_id']}")
    b.button(text="➕ Tambah channel", callback_data="ch:add")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(1)
    text = "📣 <b>Channel kamu</b>" if chans else "Belum ada channel terhubung."
    await respond(event, text, b.as_markup())


@router.message(Command("channels"))
async def channels_cmd(message: Message, db: Database) -> None:
    await show_channels(message, db)


@router.callback_query(F.data == "chs")
async def channels_cb(cb: CallbackQuery, db: Database) -> None:
    await show_channels(cb, db)


# ---------------------------------------------------------------- tambah channel
@router.callback_query(F.data == "ch:add")
async def add_start(cb: CallbackQuery, state: FSMContext, api: RawAPI) -> None:
    await cb.answer()
    await state.set_state(AddChannel.waiting)
    # Keyboard "request_chat": Telegram menampilkan daftar channel yang kamu kelola dan
    # menawarkan menambahkan bot sebagai admin dengan hak yang diminta.
    keyboard = {
        "keyboard": [[{
            "text": "📣 Pilih channel",
            "request_chat": {
                "request_id": REQUEST_ID,
                "chat_is_channel": True,
                "user_administrator_rights": {"can_post_messages": True},
                "bot_administrator_rights": {
                    "can_post_messages": True,
                    "can_edit_messages": True,
                    "can_delete_messages": True,
                },
            },
        }]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
    await api.call(
        "sendMessage", chat_id=cb.from_user.id, parse_mode="HTML", reply_markup=keyboard,
        text=(
            "Tekan <b>📣 Pilih channel</b> di bawah, lalu pilih channel yang kamu kelola. "
            "Telegram akan menawarkan menambahkan bot sebagai admin.\n\n"
            "Alternatif: <b>forward satu post</b> dari channel, atau kirim <code>@username_channel</code>.\n"
            "Batal: /cancel"
        ),
    )


async def _register(message: Message, bot: Bot, db: Database, chat_id: int | str, state: FSMContext | None) -> None:
    uid = message.from_user.id  # type: ignore[union-attr]
    chat, problems, warnings = await verify_channel(bot, chat_id, uid)
    if problems:
        await message.answer("⚠️ " + "\n⚠️ ".join(problems) + "\n\nPerbaiki lalu coba lagi.", reply_markup=_remove_kb())
        return
    assert chat is not None
    await db.add_channel(uid, chat.id, chat.title or str(chat.id), chat.username)
    if state:
        await state.clear()
    text = f"✅ Channel <b>{esc(chat.title or '')}</b> terhubung."
    if warnings:
        text += "\n\n" + "\n".join("ℹ️ " + w for w in warnings)
    await message.answer(text, reply_markup=_remove_kb())
    await message.answer("Sekarang kirim konten untuk dipost.", reply_markup=main_menu_kb())


def _remove_kb():
    from aiogram.types import ReplyKeyboardRemove
    return ReplyKeyboardRemove()


@router.message(F.chat_shared)
async def on_chat_shared(message: Message, bot: Bot, db: Database, state: FSMContext) -> None:
    await _register(message, bot, db, message.chat_shared.chat_id, state)  # type: ignore[union-attr]


@router.message(AddChannel.waiting, F.forward_origin)
async def on_forward(message: Message, bot: Bot, db: Database, state: FSMContext) -> None:
    origin = message.forward_origin
    chat = getattr(origin, "chat", None)
    if chat is None or chat.type != "channel":
        await message.answer("Itu bukan post dari channel. Forward post dari channel yang ingin dihubungkan.")
        return
    await _register(message, bot, db, chat.id, state)


@router.message(AddChannel.waiting, F.text.regexp(r"^@?[A-Za-z][A-Za-z0-9_]{3,}$"))
async def on_username(message: Message, bot: Bot, db: Database, state: FSMContext) -> None:
    uname = message.text.strip()  # type: ignore[union-attr]
    await _register(message, bot, db, uname if uname.startswith("@") else "@" + uname, state)


# ---------------------------------------------------------------- detail channel
@router.callback_query(F.data.regexp(r"^ch:-?\d+$"))
async def channel_detail(cb: CallbackQuery, bot: Bot, db: Database) -> None:
    chat_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    ch = await db.get_channel(cb.from_user.id, chat_id)
    if not ch:
        await cb.answer("Channel tidak ditemukan", show_alert=True)
        return
    _, problems, warnings = await verify_channel(bot, chat_id, cb.from_user.id)
    try:
        members = await bot.get_chat_member_count(chat_id)
    except TelegramAPIError:
        members = "?"
    lines = [f"📣 <b>{esc(ch['title'])}</b>"]
    if ch.get("username"):
        lines.append(f"@{ch['username']}")
    lines.append(f"👥 Subscriber: {members}")
    if problems:
        lines += ["", *("⚠️ " + p for p in problems)]
    else:
        lines += ["", "✅ Izin bot OK"]
    lines += ["", *("ℹ️ " + w for w in warnings)]
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Cek ulang", callback_data=f"ch:{chat_id}")
    b.button(text="🗑 Lepas channel", callback_data=f"chd:{chat_id}")
    b.button(text="⬅️ Kembali", callback_data="chs")
    b.adjust(2, 1)
    await respond(cb, "\n".join(lines), b.as_markup())


@router.callback_query(F.data.regexp(r"^chd:-?\d+$"))
async def channel_remove(cb: CallbackQuery, db: Database) -> None:
    await db.remove_channel(cb.from_user.id, int(cb.data.split(":")[1]))  # type: ignore[union-attr]
    await cb.answer("Channel dilepas dari bot ini")
    await show_channels(cb, db)


# ---------------------------------------------------------------- bot dijadikan admin / dikeluarkan
@router.my_chat_member(F.chat.type == "channel")
async def on_membership(event: ChatMemberUpdated, bot: Bot, db: Database, settings: Settings) -> None:
    new = event.new_chat_member
    if new.status == "administrator" and getattr(new, "can_post_messages", False):
        user = event.from_user
        if settings.allowed_users and user.id not in settings.allowed_users:
            return
        try:
            um = await bot.get_chat_member(event.chat.id, user.id)
        except TelegramAPIError:
            return
        if um.status not in ("creator", "administrator"):
            return
        await db.add_channel(user.id, event.chat.id, event.chat.title or str(event.chat.id), event.chat.username)
        try:
            await bot.send_message(user.id, f"✅ Channel <b>{esc(event.chat.title or '')}</b> otomatis terhubung.")
        except TelegramAPIError:
            pass  # user belum pernah /start
    elif new.status in ("left", "kicked"):
        await db.remove_channel_everywhere(event.chat.id)
