"""
============================================================
 handlers.py — Aiogram Bot Handlerlari
============================================================
 Foydalanuvchi xabarlarini qabul qilib, mos javoblarni
 qaytaradi. Har bir handler bir xil asinxron pattern
 bo'yicha ishlaydi:

   1. Foydalanuvchi ruxsatini tekshirish
   2. Havolani parse qilish
   3. "Yuklanmoqda..." xabari yuborish
   4. Userbot orqali medialni serverga yuklash
   5. Aiogram orqali foydalanuvchiga yuborish
   6. Lokal faylni o'chirish (try...finally ichida)
   7. Progress xabarini o'chirish
============================================================
"""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    FSInputFile,
    CallbackQuery,
)
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
)

from config import config
from utils import parse_telegram_link, get_media_type, MediaType, logger
from userbot import (
    userbot,
    UserbotError,
    AccessDeniedError,
    MessageNotFoundError,
    MediaTooLargeError,
    NoMediaError,
)
from web.storage import add_log, is_user_banned

# ─── Router sozlamasi ────────────────────────────────────────────────────────

router = Router(name="media_router")

# Foydalanuvchi ID lari bo'yicha rate limiting (oddiy in-memory)
# {user_id: asyncio.Lock()} — bir foydalanuvchi bir vaqtda bitta so'rov
_user_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    """Har bir foydalanuvchi uchun bitta Lock qaytaradi."""
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _is_allowed(user_id: int) -> bool:
    """Foydalanuvchi ruxsatga ega ekanligini tekshiradi."""
    if not config.allowed_users:
        return True  # Ro'yxat bo'sh → hamma ruxsatli
    return user_id in config.allowed_users


# ─── /start va /help handleri ────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Botni ishga tushirganda salomlashish xabarini yuboradi."""
    user_name = message.from_user.first_name or "Foydalanuvchi"
    await message.answer(
        f"👋 Salom, <b>{user_name}</b>!\n\n"
        "🔐 Men yopiq Telegram kanallardan media yuklab beruvchi botman.\n\n"
        "<b>Qanday foydalanish:</b>\n"
        "1. Yopiq kanaldan xabar havolasini nusxalab oling\n"
        "2. Havolani menga yuboring\n"
        "3. Media yuklab olinishi kutib turing\n\n"
        "<b>Havola formati:</b>\n"
        "<code>https://t.me/c/1234567890/456</code>\n\n"
        "📎 /help — batafsil yordam",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Yordam matni yuboradi."""
    await message.answer(
        "📖 <b>Foydalanish qo'llanmasi</b>\n\n"
        "<b>Qo'llab-quvvatlanadigan media turlari:</b>\n"
        "🖼 Rasm (Photo)\n"
        "🎥 Video\n"
        "🎵 Audio\n"
        "🎙 Ovozli xabar (Voice)\n"
        "📹 Video xabar (Video note)\n"
        "📄 Hujjat (Document)\n\n"
        "<b>Havola formatlari:</b>\n"
        "• Yopiq kanal: <code>https://t.me/c/1234567890/456</code>\n"
        "• Ochiq kanal: <code>https://t.me/username/100</code>\n\n"
        "<b>Muhim:</b>\n"
        "• Userbot yopiq kanalda a'zo bo'lishi shart\n"
        "• Bir vaqtda bitta so'rov bajariladi\n"
        "• Katta fayllar biroz vaqt talab qilishi mumkin",
        parse_mode="HTML",
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Admin panelni Web App sifatida ochish uchun tugma yuboradi."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.types.web_app_info import WebAppInfo
    import os

    # Ruxsatni tekshirish (faqat ruxsatli yoki hamma ruxsatli bo'lsa)
    if not _is_allowed(message.from_user.id):
        await message.answer("🚫 Sizda admin panelga kirish huquqi yo'q.")
        return

    # Railway bergan domain yoki localhost
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if domain:
        url = f"https://{domain}/"
    else:
        # Default fallback
        url = os.getenv("WEBAPP_URL", "https://beneficial-adventure-production.up.railway.app/")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Admin Panelni Ochish", web_app=WebAppInfo(url=url))]
        ]
    )

    await message.answer(
        "⚙️ <b>Admin Panel</b>\n\n"
        "Quyidagi tugmani bosib Web App orqali botni boshqaring:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ─── Asosiy media yuklovchi handler ─────────────────────────────────────────

@router.message(F.text)
async def handle_link(message: Message) -> None:
    """
    Foydalanuvchi yuborgan matndan Telegram havolasini topib,
    medialni yuklab, foydalanuvchiga yuboradi.

    Xavfsizlik va resurslarni tejash:
      - Faqat ruxsatli foydalanuvchilar
      - Bir foydalanuvchi bir vaqtda bitta so'rov (Lock)
      - Fayl har doim o'chiriladi (try...finally)
    """
    user_id   = message.from_user.id
    user_name = message.from_user.first_name or str(user_id)
    text      = message.text or ""

    # ── 1. Ruxsatni tekshirish ─────────────────────────────────────────────
    if not _is_allowed(user_id) or is_user_banned(user_id):
        logger.warning(f"🚫 Ruxsatsiz kirish urinishi (yoki ban): {user_id} (@{message.from_user.username})")
        await message.answer("🚫 Sizda bu botdan foydalanish huquqi yo'q yoki admin tomonidan taqiqlangansiz.")
        return

    # ── 2. Havolani tahlil qilish ──────────────────────────────────────────
    parsed = parse_telegram_link(text)
    if parsed is None:
        await message.answer(
            "❓ Telegram kanal havolasi aniqlanmadi.\n\n"
            "To'g'ri format:\n"
            "<code>https://t.me/c/1234567890/456</code>",
            parse_mode="HTML",
        )
        return

    # ── 3. Bir foydalanuvchi — bir vaqtda bitta so'rov ──────────────────────
    user_lock = _get_user_lock(user_id)
    if user_lock.locked():
        await message.answer(
            "⏳ Oldingi so'rovingiz hali tugallanmagan.\n"
            "Iltimos, biroz kuting."
        )
        return

    async with user_lock:
        logger.info(
            f"📨 So'rov qabul qilindi: user={user_id} ({user_name}), "
            f"chat={parsed.chat_id}, msg={parsed.message_id}"
        )

        # ── 4. "Yuklanmoqda..." xabarini yuborish ─────────────────────────
        progress_msg = await message.answer(
            "⏳ <b>Yuklanmoqda...</b>\n\n"
            "📡 Xabar olinmoqda...",
            parse_mode="HTML",
        )

        downloaded_path: Path | None = None

        try:
            # ── 5. Userbot orqali media yuklab olish ──────────────────────
            await _edit_progress(progress_msg, "📥 Media serverga yuklanmoqda...")
            downloaded_path = await userbot.fetch_and_download(parsed)

            # ── 6. Aiogram orqali foydalanuvchiga yuborish ─────────────────
            await _edit_progress(progress_msg, "📤 Sizga yuborilmoqda...")
            await _send_file_to_user(message, downloaded_path)

            logger.info(
                f"✅ Muvaffaqiyatli yuborildi: user={user_id}, "
                f"fayl={downloaded_path.name}"
            )
            add_log(user_id, user_name, text, "success")

        except AccessDeniedError as e:
            logger.warning(f"AccessDeniedError: {e}")
            await _edit_progress(progress_msg, str(e))
            add_log(user_id, user_name, text, "error", str(e))

        except MessageNotFoundError as e:
            logger.warning(f"MessageNotFoundError: {e}")
            await _edit_progress(progress_msg, str(e))
            add_log(user_id, user_name, text, "error", str(e))

        except NoMediaError as e:
            logger.info(f"NoMediaError: {e}")
            await _edit_progress(progress_msg, str(e))
            add_log(user_id, user_name, text, "error", str(e))

        except MediaTooLargeError as e:
            logger.warning(f"MediaTooLargeError: {e}")
            await _edit_progress(progress_msg, str(e))
            add_log(user_id, user_name, text, "error", str(e))

        except UserbotError as e:
            logger.error(f"UserbotError: {e}")
            await _edit_progress(
                progress_msg,
                f"⚠️ Userbot xatosi:\n{e}\n\nQayta urinib ko'ring."
            )
            add_log(user_id, user_name, text, "error", str(e))

        except TelegramNetworkError as e:
            logger.error(f"TelegramNetworkError (yuborishda): {e}", exc_info=True)
            await _edit_progress(
                progress_msg,
                "🌐 Tarmoq xatosi yuz berdi. Biroz kutib, qayta urinib ko'ring."
            )
            add_log(user_id, user_name, text, "error", "Network Error")

        except TelegramAPIError as e:
            logger.error(f"TelegramAPIError (yuborishda): {e}", exc_info=True)
            await _edit_progress(
                progress_msg,
                f"📡 Telegram xatosi: {e.message}\n\nQayta urinib ko'ring."
            )
            add_log(user_id, user_name, text, "error", f"API Error: {e.message}")

        except Exception as e:
            logger.exception(f"Kutilmagan xato: {e}")
            await _edit_progress(
                progress_msg,
                "💥 Kutilmagan xato yuz berdi.\n"
                "Administrator bilan bog'laning yoki qayta urinib ko'ring."
            )
            add_log(user_id, user_name, text, "error", str(e))

        finally:
            # ── 7. Disk tozalash — SHART! ─────────────────────────────────
            # Xatolik bo'lsa ham, muvaffaqiyatli bo'lsa ham fayl o'chiriladi
            if downloaded_path and downloaded_path.exists():
                try:
                    os.remove(downloaded_path)
                    logger.info(f"🗑️  Vaqtinchalik fayl o'chirildi: {downloaded_path.name}")
                except OSError as remove_err:
                    logger.error(
                        f"Faylni o'chirishda xato: {downloaded_path} — {remove_err}"
                    )


# ─── Yordamchi funksiyalar ───────────────────────────────────────────────────

async def _edit_progress(progress_msg: Message, text: str) -> None:
    """
    Progress xabarini yangilaydi. Xatolik bo'lsa (xabar o'chgan va h.k.)
    logga yozadi, lekin asosiy operatsiyani to'xtatmaydi.
    """
    try:
        await progress_msg.edit_text(text, parse_mode="HTML")
    except Exception as e:
        logger.debug(f"Progress xabarini yangilashda xato (muhim emas): {e}")


async def _send_file_to_user(message: Message, file_path: Path) -> None:
    """
    Faylni aiogram FSInputFile orqali foydalanuvchiga yuboradi.
    Media turiga qarab mos Telegram usulini (send_photo, send_video va h.k.) tanlaydi.

    Args:
        message: Asl foydalanuvchi xabari (reply uchun ishlatiladi).
        file_path: Yuklab olingan faylning yo'li.

    Raises:
        TelegramAPIError: Fayl yuborishda Telegram API xatosi.
    """
    input_file = FSInputFile(path=file_path)
    suffix = file_path.suffix.lower()

    # Fayl kengaytmasi bo'yicha media turini aniqlash
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        await message.answer_photo(
            photo=input_file,
            caption="✅ Mana sizning rasmingiz!",
        )

    elif suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        await message.answer_video(
            video=input_file,
            caption="✅ Mana sizning videongiz!",
            supports_streaming=True,
        )

    elif suffix in {".mp3", ".flac", ".ogg", ".aac", ".wav", ".m4a"}:
        await message.answer_audio(
            audio=input_file,
            caption="✅ Mana sizning audiongiz!",
        )

    elif suffix == ".oga":
        # Telegram voice xabarlari .oga formatida keladi
        await message.answer_voice(
            voice=input_file,
        )

    elif suffix in {".mp4v"} or "video_note" in file_path.name:
        await message.answer_video_note(video_note=input_file)

    else:
        # Boshqa barcha formatlar — hujjat sifatida yuborish
        await message.answer_document(
            document=input_file,
            caption="✅ Mana sizning faylingiz!",
        )
