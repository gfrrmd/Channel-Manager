from __future__ import annotations

import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import load_settings
from .db import Database
from .handlers import channels, compose, posts, start, votes
from .scheduler import run_scheduler
from .tgapi import RawAPI
from .ui import AccessMiddleware

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="post", description="Buat post baru"),
    BotCommand(command="channels", description="Kelola channel"),
    BotCommand(command="scheduled", description="Post terjadwal"),
    BotCommand(command="posts", description="Riwayat post"),
    BotCommand(command="tz", description="Atur zona waktu"),
    BotCommand(command="cancel", description="Batalkan proses"),
    BotCommand(command="help", description="Bantuan"),
]


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(settings.database_url)
    await db.connect()
    api = RawAPI(settings.bot_token)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    dp = Dispatcher(storage=MemoryStorage(), settings=settings, db=db, api=api)
    access = AccessMiddleware(settings)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    # urutan penting: handler state-spesifik & command lebih dulu, catch-all konten terakhir
    dp.include_routers(start.router, channels.router, votes.router, posts.router, compose.router)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    me = await bot.me()
    log.info("Berjalan sebagai @%s (rich_mode=%s)", me.username, settings.rich_mode)
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands(COMMANDS)

    scheduler_task = asyncio.create_task(run_scheduler(bot, api, db, settings, stop))
    polling = asyncio.create_task(
        dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "my_chat_member"],
            handle_signals=False,
        )
    )
    await stop.wait()
    log.info("Menutup…")
    await dp.stop_polling()
    await asyncio.gather(polling, scheduler_task, return_exceptions=True)
    await bot.session.close()
    await api.close()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
