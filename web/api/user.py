from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from database import async_session, User, DownloadHistory, UserTariff, Tariff
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from web.auth import get_current_user_id

router = APIRouter(prefix="/api/user", tags=["user"])

class HistoryItem(BaseModel):
    id: int
    file_name: Optional[str]
    file_size_bytes: int
    created_at: datetime
    
class CurrentTariff(BaseModel):
    name: str
    total_gb: float
    used_gb: float
    days_left: str

class UserProfile(BaseModel):
    id: int
    first_name: str
    balance: float
    auto_compress: bool
    save_to_saved_messages: bool
    tariff: CurrentTariff

@router.get("/me", response_model=UserProfile)
async def get_my_profile(user_id: int = Depends(get_current_user_id)):
    """User Dashboard uchun asosiy profil ma'lumotlari."""
    async with async_session() as db:
        # Load user with tariff
        result = await db.execute(
            select(User)
            .where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        # Get active tariff (safely handles multiple expired ones)
        tariff_res = await db.execute(
            select(UserTariff)
            .options(selectinload(UserTariff.tariff)) # relationship
            .where(UserTariff.user_id == user_id)
            .order_by(UserTariff.expires_at.desc())
            .limit(1)
        )
        user_tariff = tariff_res.scalars().first()
        
        # Get total downloaded today/month (mocking total gb for now since we just track size)
        downloads_res = await db.execute(
            select(DownloadHistory).where(DownloadHistory.user_id == user_id)
        )
        downloads = downloads_res.scalars().all()
        used_bytes = sum([(d.file_size_bytes or 0) for d in downloads])
        used_gb = used_bytes / (1024 * 1024 * 1024)
        
        if user_tariff and user_tariff.expires_at > datetime.utcnow():
            t = user_tariff.tariff
            days_left = str((user_tariff.expires_at - datetime.utcnow()).days)
            tariff_info = CurrentTariff(
                name=t.name,
                total_gb=t.max_file_size_bytes / (1024*1024*1024),
                used_gb=used_gb,
                days_left=days_left
            )
        else:
            # Boshlang'ich (tekin) tarif
            tariff_info = CurrentTariff(
                name="Boshlang'ich (Tekin)",
                total_gb=2.0,
                used_gb=used_gb,
                days_left="Cheksiz"
            )

        return UserProfile(
            id=user.id,
            first_name=user.first_name,
            balance=user.balance,
            auto_compress=user.auto_compress,
            save_to_saved_messages=user.save_to_saved_messages,
            tariff=tariff_info
        )

@router.get("/history", response_model=List[HistoryItem])
async def get_my_history(user_id: int = Depends(get_current_user_id)):
    """User Dashboard uchun oxirgi yuklamalar."""
    async with async_session() as db:
        result = await db.execute(
            select(DownloadHistory)
            .where(DownloadHistory.user_id == user_id)
            .order_by(DownloadHistory.created_at.desc())
            .limit(10)
        )
        history = result.scalars().all()
        return [
            HistoryItem(
                id=h.id,
                file_name=h.file_name or "Noma'lum fayl",
                file_size_bytes=h.file_size_bytes,
                created_at=h.created_at
            ) for h in history
        ]

class UserSettings(BaseModel):
    autoCompress: bool
    saveToSaved: bool

@router.post("/settings")
async def update_my_settings(settings: UserSettings, user_id: int = Depends(get_current_user_id)):
    """User Dashboard orqali sozlamalarni saqlaydi."""
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        user.auto_compress = settings.autoCompress
        user.save_to_saved_messages = settings.saveToSaved
        await db.commit()
        
        return {"status": "success"}

class DownloadRequest(BaseModel):
    link: str

async def _do_download(user_id: int, user_first_name: str, link: str):
    """Web App orqali yuborilgan havolani yuklab, bot orqali foydalanuvchiga yuboradi."""
    from bot_instance import bot
    from utils import parse_telegram_link
    from userbot import userbot, AccessDeniedError, MessageNotFoundError, NoMediaError, MediaTooLargeError, UserbotError
    from database import async_session, DownloadHistory
    import logging
    logger = logging.getLogger(__name__)

    parsed = parse_telegram_link(link)
    if parsed is None:
        await bot.send_message(user_id, "❓ Havola noto'g'ri formatda.\n\nTo'g'ri format:\n<code>https://t.me/c/1234567890/456</code>", parse_mode="HTML")
        return

    progress_msg = await bot.send_message(user_id, "⏳ <b>Yuklanmoqda...</b>\n📡 Xabar olinmoqda...", parse_mode="HTML")
    downloaded_path = None
    try:
        import time
        from utils import human_readable_size
        last_edit_time = 0

        async def on_download_progress(current: int, total: int, *args, **kwargs):
            nonlocal last_edit_time
            now = time.time()
            if now - last_edit_time > 2.0:
                last_edit_time = now
                pct = (current / total * 100) if total else 0
                c_str = human_readable_size(current)
                t_str = human_readable_size(total) if total else "?"
                import asyncio
                
                async def _update_ui():
                    try:
                        await bot.edit_message_text(
                            user_id, progress_msg.message_id, 
                            f"📥 <b>Media serverga yuklanmoqda...</b>\n\n"
                            f"📊 {pct:.1f}%\n"
                            f"💾 {c_str} / {t_str}", 
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                asyncio.create_task(_update_ui())

        await bot.edit_message_text(user_id, progress_msg.message_id, "📥 <b>Media serverga yuklanmoqda...</b>", parse_mode="HTML")
        downloaded_path, media_type = await userbot.fetch_and_download(user_id, parsed, progress_callback=on_download_progress)

        # DB ga tarix yozish
        async with async_session() as db:
            from database import DownloadHistory
            db.add(DownloadHistory(
                user_id=user_id,
                file_name=downloaded_path.name,
                file_size_bytes=downloaded_path.stat().st_size
            ))
            await db.commit()

        await bot.edit_message_text(user_id, progress_msg.message_id, "📤 <b>Sizga yuborilmoqda...</b>", parse_mode="HTML")

        from aiogram.types import FSInputFile
        from utils import MediaType

        file = FSInputFile(downloaded_path)
        caption = "✅ Mana sizning faylingiz!"

        if media_type == MediaType.VIDEO:
            await bot.send_video(user_id, file, caption=caption, supports_streaming=True)
        elif media_type == MediaType.PHOTO:
            await bot.send_photo(user_id, file, caption=caption)
        elif media_type == MediaType.AUDIO:
            await bot.send_audio(user_id, file, caption=caption)
        elif media_type == MediaType.VOICE:
            await bot.send_voice(user_id, file)
        elif media_type == MediaType.VIDEO_NOTE:
            await bot.send_video_note(user_id, file)
        else:
            await bot.send_document(user_id, file, caption=caption)

        await bot.delete_message(user_id, progress_msg.message_id)
        logger.info(f"✅ Web App yuklash: user={user_id}, fayl={downloaded_path.name}")

    except AccessDeniedError as e:
        await bot.edit_message_text(user_id, progress_msg.message_id, f"🚫 Kirish taqiqlangan!\n{e}\n\nAkkauntingiz kanalga a'zo ekanligini tekshiring.")
    except MessageNotFoundError as e:
        await bot.edit_message_text(user_id, progress_msg.message_id, f"❌ Xabar topilmadi: {e}")
    except NoMediaError:
        await bot.edit_message_text(user_id, progress_msg.message_id, "⚠️ Bu xabarda yuklanadigan media yo'q.")
    except UserbotError as e:
        await bot.edit_message_text(user_id, progress_msg.message_id,
            f"⚠️ Sessiyangiz ulanmagan!\n\nWeb App → Ulanish bo'limidan Telegram akkauntingizni ulang.", parse_mode="HTML")
        logger.error(f"UserbotError: {e}")
    except Exception as e:
        logger.error(f"Web download xatolik: {e}", exc_info=True)
        try:
            await bot.edit_message_text(user_id, progress_msg.message_id, f"❌ Kutilmagan xatolik: {e}")
        except Exception:
            pass
    finally:
        if downloaded_path and downloaded_path.exists():
            downloaded_path.unlink(missing_ok=True)


@router.post("/download")
async def request_download(req: DownloadRequest, user_id: int = Depends(get_current_user_id)):
    """Web app orqali yuborilgan havolani qabul qilib, yuklash jarayonini boshlaydi."""
    import asyncio
    from database import async_session, User
    from sqlalchemy import select

    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user or user.is_banned:
            raise HTTPException(status_code=403, detail="Ruxsat etilmagan foydalanuvchi")

    asyncio.create_task(_do_download(user_id, user.first_name, req.link))
    return {"status": "success"}
