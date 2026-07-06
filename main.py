"""
============================================================
 main.py — Dasturning Kirish Nuqtasi (Entry Point)
============================================================
 Ishga tushirish:  python main.py

 Bu fayl quyidagilarni amalga oshiradi:
   1. Aiogram Dispatcher va Bot ob'ektlarini yaratish
   2. Handlerlari ro'yxatdan o'tkazish
   3. Pyrogram userbot ni asinxron ishga tushirish
   4. Aiogram polling ni boshlash
   5. SIGINT/SIGTERM signallari kelganda xavfsiz to'xtatish
============================================================
"""

import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from handlers import router
from userbot import userbot
import os
import uvicorn
from web.app import app as web_app

logger = logging.getLogger(__name__)


# ─── Dastur holati ───────────────────────────────────────────────────────────

_shutdown_event = asyncio.Event()


# ─── Ishga tushirish va to'xtatish ──────────────────────────────────────────

async def on_startup(bot: Bot) -> None:
    """
    Bot ishga tushganda bir marta chaqiriladi.
    Userbot ni ishga tushiradi va loglarga info yozadi.
    """
    logger.info("=" * 60)
    logger.info("  Telegram Media Bot ishga tushmoqda")
    logger.info("=" * 60)

    # Pyrogram userbot ni ishga tushirish
    await userbot.start()

    # Bot ma'lumotlarini olish
    me = await bot.get_me()
    logger.info(f"🤖 Aiogram Bot: @{me.username} (ID: {me.id})")
    logger.info(f"📂 Yuklamalar papkasi: {config.download_dir.resolve()}")

    if config.allowed_users:
        logger.info(f"🔐 Ruxsatli foydalanuvchilar: {config.allowed_users}")
    else:
        logger.info("🌐 Kirish cheklovlari: Hamma foydalanuvchilar ruxsatli")

    logger.info("✅ Bot tayyor! Polling boshlandi...")
    logger.info("=" * 60)


async def on_shutdown(bot: Bot) -> None:
    """
    Bot to'xtatilganda chaqiriladi.
    Resurslarni xavfsiz ozod qiladi.
    """
    logger.info("🛑 Bot to'xtatilmoqda...")

    # Pyrogram clientini yopish
    await userbot.stop()

    # Aiogram session ni yopish
    await bot.session.close()

    logger.info("✅ Bot muvaffaqiyatli to'xtatildi.")


def _setup_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """
    UNIX signal handlerlari (Linux/macOS uchun).
    Windows da signal.SIGTERM qo'llab-quvvatlanmasligi mumkin,
    lekin xatolik bermaydi.
    """
    def _signal_handler(sig_name: str):
        logger.info(f"📶 Signal qabul qilindi: {sig_name}")
        _shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler, sig.name)
        except NotImplementedError:
            # Windows da SIGTERM uchun add_signal_handler ishlamaydi
            logger.debug(f"Signal {sig.name} uchun handler o'rnatilmadi (Windows?)")


# ─── Asosiy asinxron funksiya ────────────────────────────────────────────────

async def main() -> None:
    """Botning asosiy asinxron tsikli."""

    # Aiogram Bot ob'ekti
    bot = Bot(
        token=config.bot.token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,  # Barcha xabarlarda HTML parse
        ),
    )

    # Dispatcher — FSM uchun MemoryStorage (oddiy botlar uchun yetarli)
    # Katta yuklamali production uchun RedisStorage tavsiya etiladi
    dp = Dispatcher(storage=MemoryStorage())

    # Handlerlari ro'yxatdan o'tkazish
    dp.include_router(router)

    # Hayot tsikli hooklari
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Signal handlerlari
    loop = asyncio.get_running_loop()
    _setup_signal_handlers(loop)

    # Polling ni boshlash
    # Web serverni orqa fonda ishga tushirish
    web_config = uvicorn.Config(web_app, host=os.getenv("WEB_HOST", "0.0.0.0"), port=int(os.getenv("WEB_PORT", "8080")))
    server = uvicorn.Server(web_config)
    asyncio.create_task(server.serve())
    
    try:
        await dp.start_polling(
            bot,
            skip_updates=True,
            allowed_updates=["message", "callback_query"],
        )
    except Exception as e:
        logger.critical(f"💥 Polling da kritik xato: {e}", exc_info=True)
        raise
    finally:
        # Agar polling ichida xato bo'lsa, userbot ham to'xtatilishi kerak
        if userbot._client and userbot._client.is_connected:
            await userbot.stop()


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Python versiyasini tekshirish
    if sys.version_info < (3, 11):
        print(
            "❌ Xato: Python 3.11 yoki yangroq versiya talab qilinadi.\n"
            f"   Sizda: Python {sys.version}"
        )
        sys.exit(1)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Ctrl+C bosib to'xtatilganda
        logger.info("\n👋 Bot to'xtatildi (Ctrl+C).")
    except ValueError as e:
        # Konfiguratsiya xatolari (config.py dan)
        logger.critical(str(e))
        sys.exit(1)
    except Exception as e:
        logger.critical(f"💥 Dastur kutilmagan xato bilan to'xtatildi: {e}", exc_info=True)
        sys.exit(1)
