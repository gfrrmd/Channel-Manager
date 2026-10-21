from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import Settings


def main_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✍️ Post baru", callback_data="np")
    b.button(text="📣 Channel", callback_data="chs")
    b.button(text="🗓 Terjadwal", callback_data="sch")
    b.button(text="📋 Riwayat", callback_data="hist")
    b.button(text="🌐 Zona waktu", callback_data="tz")
    b.adjust(2, 2, 1)
    return b.as_markup()


def back_kb(data: str = "menu", text: str = "⬅️ Menu") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=text, callback_data=data)
    return b.as_markup()


async def respond(event: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Edit pesan bila dipicu callback, kirim pesan baru bila dipicu command."""
    if isinstance(event, CallbackQuery):
        await event.answer()
        try:
            await event.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]
            return
        except TelegramBadRequest:
            await event.message.answer(text, reply_markup=kb)  # type: ignore[union-attr]
    else:
        await event.answer(text, reply_markup=kb)


class AccessMiddleware(BaseMiddleware):
    """Batasi pemakaian ke ALLOWED_USERS (kosong = semua boleh) dan hanya chat privat."""

    def __init__(self, settings: Settings) -> None:
        self.allowed = settings.allowed_users

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        msg = event if isinstance(event, Message) else getattr(event, "message", None)
        if msg is not None and msg.chat.type != "private":
            return None
        if user is None:
            return None
        if self.allowed and user.id not in self.allowed:
            if isinstance(event, CallbackQuery):
                await event.answer("Akses ditolak", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("⛔ Bot ini bersifat privat.")
            return None
        return await handler(event, data)
