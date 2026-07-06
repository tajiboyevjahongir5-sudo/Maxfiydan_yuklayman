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
import os
import signal
import sys

import uvicorn
from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from handlers import router
from payment_handler import payment_router
from userbot import userbot
from web.app import app as web_app
from bot_instance import bot

logger = logging.getLogger(__name__)


# ─── Dastur holati ───────────────────────────────────────────────────────────

_shutdown_event = asyncio.Event()


# ─── Ishga tushirish va to'xtatish ──────────────────────────────────────────

from database import init_db

async def on_startup(bot: Bot) -> None:
    """
    Bot ishga tushganda chaqiriladi.
    Barcha resurslarni (Userbot, xotira, db) tayyorlaydi.
    """
    logger.info("=" * 60)
    logger.info("🚀 Bot ishga tushmoqda...")

    # Bazani tayyorlash
    logger.info("🗄 Ma'lumotlar bazasi tayyorlanmoqda...")
    await init_db()

    # Pyrogram userbot ni ishga tushirish (SessionManager)
    await userbot.start_all()

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

    # Pyrogram sessiyalarini yopish
    await userbot.stop_all()

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

    # Dispatcher — FSM uchun MemoryStorage (oddiy botlar uchun yetarli)
    # Katta yuklamali production uchun RedisStorage tavsiya etiladi
    dp = Dispatcher(storage=MemoryStorage())

    # Handlerlari ro'yxatdan o'tkazish
    dp.include_router(router)
    dp.include_router(payment_router)

    # Hayot tsikli hooklari
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Signal handlerlari
    loop = asyncio.get_running_loop()
    _setup_signal_handlers(loop)

    # Web serverni orqa fonda ishga tushirish
    # Railway PORT env ni ishlatadi, yo'q bo'lsa WEB_PORT, keyin 8080
    port = int(os.getenv("PORT") or os.getenv("WEB_PORT") or "8080")
    web_config = uvicorn.Config(web_app, host="0.0.0.0", port=port)
    server = uvicorn.Server(web_config)
    asyncio.create_task(server.serve())
    
    try:
        # Eski webhooklarni o'chirish
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Polling boshlash
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "channel_post"],
        )
    except Exception as e:
        logger.critical(f"💥 Polling da kritik xato: {e}", exc_info=True)
        raise
    finally:
        await userbot.stop_all()


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
