import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, func, and_
from database import async_session, User, UserTariff, Tariff, DownloadHistory

class LimitExceededError(Exception):
    pass

async def check_download_limits(user_id: int) -> None:
    """
    Foydalanuvchining joriy tarifi bo'yicha yuklash limitlarini tekshiradi.
    Limitdan o'tgan bo'lsa LimitExceededError xatosini qaytaradi.
    """
    async with async_session() as db:
        # User ni olish
        user_res = await db.execute(select(User).where(User.id == user_id))
        user = user_res.scalar_one_or_none()
        if not user:
            raise LimitExceededError("Foydalanuvchi topilmadi.")

        # Aktiv tarifini izlash
        tariff_res = await db.execute(
            select(UserTariff)
            .where(UserTariff.user_id == user_id)
            .where(UserTariff.expires_at > datetime.utcnow())
            .order_by(UserTariff.expires_at.desc())
            .limit(1)
        )
        user_tariff = tariff_res.scalars().first()

        # Bugungi yuklamalar soni
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_count_res = await db.execute(
            select(func.count(DownloadHistory.id))
            .where(DownloadHistory.user_id == user_id)
            .where(DownloadHistory.created_at >= today_start)
        )
        today_count = today_count_res.scalar() or 0

        # Jami ishlatilgan hajm
        downloads_res = await db.execute(
            select(DownloadHistory).where(DownloadHistory.user_id == user_id)
        )
        downloads = downloads_res.scalars().all()
        used_bytes = sum([(d.file_size_bytes or 0) for d in downloads])

        if user_tariff:
            t = user_tariff.tariff
            if t.max_downloads_per_day != -1 and today_count >= t.max_downloads_per_day:
                raise LimitExceededError(f"Siz bugun {t.max_downloads_per_day} ta fayl yukladingiz. Kunlik limit tugadi.")
            
            if used_bytes >= t.max_file_size_bytes:
                max_gb = t.max_file_size_bytes / (1024*1024*1024)
                raise LimitExceededError(f"Sizning tarifingiz hajmi ({max_gb:.1f} GB) tugadi. Yangi tarif xarid qiling.")
        else:
            # Boshlang'ich (tekin) tarif qoidalari
            # 500 MB limit, 3 kun, jami 3 ta yuklash
            max_bytes = 500 * 1024 * 1024
            max_total_downloads = 3
            days_allowed = 3

            # 1. Muddat
            if (datetime.utcnow() - user.registered_at).days >= days_allowed:
                raise LimitExceededError("Sizning 3 kunlik tekin sinov muddatingiz tugagan. Davom etish uchun tarif xarid qiling.")
            
            # 2. Hajm
            if used_bytes >= max_bytes:
                raise LimitExceededError("Siz 500 MB lik tekin limitni ishlatib bo'ldingiz. Yangi tarif xarid qiling.")
            
            # 3. Jami yuklama soni
            total_downloads_count = len(downloads)
            if total_downloads_count >= max_total_downloads:
                raise LimitExceededError(f"Tekin tarifda jami {max_total_downloads} ta fayl yuklash mumkin xolos. Limit tugadi.")
