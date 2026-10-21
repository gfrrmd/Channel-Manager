from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import Settings
from ..db import Database
from ..ui import back_kb, main_menu_kb, respond
from ..utils import esc, valid_zone

router = Router(name="start")

WELCOME = (
    "👋 <b>Channel Manager Bot</b>\n\n"
    "Kirim teks, foto, video, atau file apa saja ke sini. Aku bantu:\n"
    "• menambahkan <b>button di dalam teks</b> (Rich Message)\n"
    "• memposting ke channel kamu, sekarang atau terjadwal\n"
    "• mengedit button, menghapus post, dan menghitung vote\n\n"
    "Mulai dengan menghubungkan channel lewat tombol <b>📣 Channel</b>."
)

HELP = (
    "<b>Perintah</b>\n"
    "/post – buat post baru\n"
    "/channels – kelola channel\n"
    "/scheduled – post terjadwal\n"
    "/posts – riwayat post\n"
    "/tz <code>Asia/Jakarta</code> – atur zona waktu\n"
    "/cancel – batalkan proses\n\n"
    "Atau langsung kirim konten apa pun ke bot ini."
)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu_kb())


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP, reply_markup=main_menu_kb())


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Dibatalkan.", reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu")
async def menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await respond(cb, WELCOME, main_menu_kb())


@router.message(Command("tz"))
async def tz_cmd(message: Message, db: Database, settings: Settings) -> None:
    arg = (message.text or "").partition(" ")[2].strip()
    uid = message.from_user.id  # type: ignore[union-attr]
    if not arg:
        cur = await db.get_tz(uid, settings.default_tz)
        await message.answer(f"Zona waktu saat ini: <b>{esc(cur)}</b>\nUbah: <code>/tz Asia/Jakarta</code>")
        return
    if not valid_zone(arg):
        await message.answer("Zona waktu tidak valid. Contoh: <code>Asia/Jakarta</code>, <code>Asia/Makassar</code>, <code>UTC</code>")
        return
    await db.set_tz(uid, arg)
    await message.answer(f"✅ Zona waktu diatur ke <b>{esc(arg)}</b>")


@router.callback_query(F.data == "tz")
async def tz_cb(cb: CallbackQuery, db: Database, settings: Settings) -> None:
    cur = await db.get_tz(cb.from_user.id, settings.default_tz)
    await respond(
        cb,
        f"Zona waktu saat ini: <b>{esc(cur)}</b>\nUbah dengan <code>/tz Asia/Jakarta</code>",
        back_kb(),
    )
