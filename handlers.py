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


import json
from database import get_settings

async def enforce_subscriptions(message: Message, user_id: int = None) -> bool:
    """
    Majburiy kanallarga a'zolikni tekshiradi.
    Agar hamma kanallarga a'zo bo'lsa True,
    Aks holda (xabar yuborib) False qaytaradi.
    """
    if user_id is None:
        user_id = message.from_user.id

    settings = await get_settings()
    if not settings or not settings.force_channels:
        return True
    
    try:
        channels = json.loads(settings.force_channels)
    except Exception:
        return True
    
    if not channels:
        return True
        
    missing = []
    for c in channels:
        try:
            member = await message.bot.get_chat_member(chat_id=c['id'], user_id=user_id)
            if member.status in ['left', 'kicked']:
                missing.append(c)
        except Exception:
            # Agar bot kanalda yo'q bo'lsa yoki ID xato bo'lsa, o'tkazib yuboramiz.
            # Yoki missing qatoriga qo'shish mumkin, lekin yaxshisi o'tkazvorish.
            pass
            
    if missing:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        buttons = []
        for i, c in enumerate(missing, 1):
            buttons.append([InlineKeyboardButton(text=f"📢 {i}-Kanalga a'zo bo'lish", url=c['url'])])
        buttons.append([InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="check_sub")])
        
        await message.answer(
            "🛑 <b>Botdan foydalanish uchun quyidagi kanallarga a'zo bo'lishingiz shart!</b>\n\n"
            "Iltimos, avval kanallarga obuna bo'ling va so'ngra <b>Tasdiqlash</b> tugmasini bosing.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
        return False
        
    return True

# ─── Callback handler for check_sub ──────────────────────────────────────────
@router.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery):
    await call.answer()
    if await enforce_subscriptions(call.message, user_id=call.from_user.id):
        # A'zo bo'lgan bo'lsa
        # Eski xabarni o'chirish (Tasdiqlash yuborilgan xabarni)
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer("✅ Rahmat! Endi botdan to'liq foydalanishingiz mumkin. Havola yuboring yoki /start ni bosing.")

# ─── /start va /help handleri ────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Botni ishga tushirganda salomlashish xabarini yuboradi va userni bazaga saqlaydi."""
    if not await enforce_subscriptions(message):
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.types.web_app_info import WebAppInfo
    from database import async_session, User
    from sqlalchemy import select
    import os

    user = message.from_user
    user_name = user.first_name or "Foydalanuvchi"

    # ── Foydalanuvchini bazaga saqlash (agar mavjud bo'lmasa) ─────────────
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user.id))
        db_user = result.scalar_one_or_none()

        if not db_user:
            new_user = User(
                id=user.id,
                first_name=user.first_name or "",
                username=user.username,
            )
            db.add(new_user)
            await db.commit()
            logger.info(f"✅ Yangi foydalanuvchi qo'shildi: {user.id} ({user_name})")
        else:
            # Ism va username yangilanishi mumkin
            if db_user.first_name != (user.first_name or ""):
                db_user.first_name = user.first_name or ""
                db_user.username = user.username
                await db.commit()

    # Railway bergan domain yoki localhost
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if domain:
        url = f"https://{domain}/user-dashboard"
    else:
        url = os.getenv("WEBAPP_URL", "https://maxfiydanyuklayman-production.up.railway.app/user-dashboard")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Asosiy Sayt (Web App)", web_app=WebAppInfo(url=url))]
        ]
    )

    await message.answer(
        f"👋 Salom, <b>{message.from_user.first_name}</b>!\n\n"
        "🔓 Men yopiq Telegram kanallardan media yuklab beruvchi botman.\n\n"
        "<b>👇 Pastdagi Web App orqali tizimga kiring va media yuklashni boshlang!</b>\n\n"
        "🔗 /help — batafsil yordam",
        reply_markup=keyboard,
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
        "• Siz o'sha maxfiy kanalda a'zo bo'lishingiz shart\n"
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

    # Ruxsatni tekshirish (Faqat admin uchun)
    if message.from_user.id != config.admin_id:
        await message.answer("🚫 Sizda admin panelga kirish huquqi yo'q.")
        return

    # Railway bergan domain yoki localhost
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if domain:
        url = f"https://{domain}/admin-panel"
    else:
        # Default fallback
        url = os.getenv("WEBAPP_URL", "https://maxfiydanyuklayman-production.up.railway.app/admin-panel")

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

    if not await enforce_subscriptions(message):
        return

    # ── 1. Ruxsatni DB orqali tekshirish ──────────────────────────────────────
    from database import async_session, User, DownloadHistory
    from sqlalchemy import select

    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            await message.answer("Siz ro'yxatdan o'tmagansiz. Iltimos, /start orqali tizimga kiring.")
            return
            
        if db_user.is_banned:
            await message.answer("🚫 Siz admin tomonidan bloklangansiz.")
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
        from limits import check_download_limits, LimitExceededError
        try:
            await check_download_limits(user_id)
        except LimitExceededError as e:
            await message.answer(f"🚫 <b>Yuklash rad etildi!</b>\n\n{str(e)}", parse_mode="HTML")
            return

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
            import time
            from utils import human_readable_size
            last_edit_time = 0

            async def on_download_progress(current: int, total: int, current_file: int = 1, total_files: int = 1, *args, **kwargs):
                nonlocal last_edit_time
                now = time.time()
                # 2 soniyada bir marta yangilash (FloodWait oldini olish uchun)
                if now - last_edit_time > 2.0:
                    last_edit_time = now
                    pct = (current / total * 100) if total else 0
                    c_str = human_readable_size(current)
                    t_str = human_readable_size(total) if total else "?"
                    
                    file_info = f" ({current_file}/{total_files})" if total_files > 1 else ""
                    
                    import asyncio
                    asyncio.create_task(_edit_progress(progress_msg, 
                        f"📥 <b>Media serverga yuklanmoqda...</b>{file_info}\n\n"
                        f"📊 {pct:.1f}%\n"
                        f"💾 {c_str} / {t_str}"
                    ))

            # ── 5. Userbot orqali media yuklab olish ──────────────────────
            await _edit_progress(progress_msg, "📥 Media serverga yuklanmoqda...")
            downloaded_files = await userbot.fetch_and_download(user_id, parsed, progress_callback=on_download_progress)

            if not downloaded_files:
                raise Exception("Fayllarni yuklab olish imkoni bo'lmadi.")

            # DB ga tarix yozish
            async with async_session() as db:
                for f_path, _ in downloaded_files:
                    history = DownloadHistory(
                        user_id=user_id,
                        file_name=f_path.name,
                        file_size_bytes=f_path.stat().st_size
                    )
                    db.add(history)
                await db.commit()

            # ── 6. Aiogram orqali foydalanuvchiga yuborish ─────────────────
            await _edit_progress(progress_msg, "📤 Sizga yuborilmoqda...")
            
            if len(downloaded_files) == 1:
                await _send_file_to_user(message, downloaded_files[0][0], downloaded_files[0][1])
            else:
                await _send_media_group_to_user(message, downloaded_files)

            logger.info(
                f"✅ Muvaffaqiyatli yuborildi: user={user_id}, "
                f"fayllar_soni={len(downloaded_files)}"
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
            # Xatolik bo'lsa ham, muvaffaqiyatli bo'lsa ham barcha fayllar o'chiriladi
            if 'downloaded_files' in locals() and downloaded_files:
                for f_path, _ in downloaded_files:
                    if f_path and f_path.exists():
                        try:
                            os.remove(f_path)
                            logger.info(f"🗑️  Vaqtinchalik fayl o'chirildi: {f_path.name}")
                        except OSError as remove_err:
                            logger.error(f"Faylni o'chirishda xato: {f_path} — {remove_err}")
            elif 'downloaded_path' in locals() and downloaded_path and downloaded_path.exists():
                try:
                    os.remove(downloaded_path)
                except OSError:
                    pass


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


async def _send_file_to_user(message: Message, file_path: Path, media_type=None) -> None:
    """
    Faylni aiogram FSInputFile orqali foydalanuvchiga yuboradi.
    Media turini Pyrogram media_type dan aniqlanadi (kengaytmadan emas).
    """
    from utils import MediaType
    input_file = FSInputFile(path=file_path)

    if media_type == MediaType.PHOTO:
        await message.answer_photo(
            photo=input_file,
            caption="✅ Mana sizning rasmingiz!",
        )
    elif media_type == MediaType.VIDEO:
        await message.answer_video(
            video=input_file,
            caption="✅ Mana sizning videongiz!",
            supports_streaming=True,
        )
    elif media_type == MediaType.AUDIO:
        await message.answer_audio(
            audio=input_file,
            caption="✅ Mana sizning audiongiz!",
        )
    elif media_type == MediaType.VOICE:
        await message.answer_voice(voice=input_file)
    elif media_type == MediaType.VIDEO_NOTE:
        await message.answer_video_note(video_note=input_file)
    else:
        # Agar media_type noma'lum bo'lsa yoki DOCUMENT — kengaytmadan ham urinib ko'ramiz
        suffix = file_path.suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            await message.answer_photo(photo=input_file, caption="✅ Mana sizning rasmingiz!")
        elif suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
            await message.answer_video(video=input_file, caption="✅ Mana sizning videongiz!", supports_streaming=True)
        elif suffix in {".mp3", ".flac", ".ogg", ".aac", ".wav", ".m4a"}:
            await message.answer_audio(audio=input_file, caption="✅ Mana sizning audiongiz!")
        elif suffix == ".oga":
            await message.answer_voice(voice=input_file)
        else:
            await message.answer_document(
                document=input_file,
                caption="✅ Mana sizning faylingiz!",
            )

async def _send_media_group_to_user(message: Message, files: list[tuple[Path, str]]) -> None:
    """
    Bir nechta faylni Media Group (Albom) sifatida yuboradi.
    """
    from aiogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument
    from utils import MediaType
    
    media_group = []
    
    for idx, (file_path, m_type) in enumerate(files):
        input_file = FSInputFile(path=file_path)
        caption = "✅ Albom yuklandi!" if idx == 0 else None
        
        if m_type == MediaType.PHOTO:
            media_group.append(InputMediaPhoto(media=input_file, caption=caption))
        elif m_type == MediaType.VIDEO:
            media_group.append(InputMediaVideo(media=input_file, caption=caption, supports_streaming=True))
        else:
            # Agar hujjat yoki boshqa bo'lsa, media guruhda ba'zida faqat photo/video ruxsat etiladi.
            # Lekin Document ham albom bo'lishi mumkin. Kengaytmadan tekshiramiz.
            suffix = file_path.suffix.lower()
            if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                media_group.append(InputMediaPhoto(media=input_file, caption=caption))
            elif suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
                media_group.append(InputMediaVideo(media=input_file, caption=caption, supports_streaming=True))
            else:
                media_group.append(InputMediaDocument(media=input_file, caption=caption))
                
    if media_group:
        # Aiogramda bitta media_group da maksimal 10 ta element bo'lishi mumkin
        for i in range(0, len(media_group), 10):
            chunk = media_group[i:i+10]
            await message.answer_media_group(media=chunk)
