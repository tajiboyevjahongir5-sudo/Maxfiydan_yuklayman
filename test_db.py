import asyncio
from database import async_session, User, DownloadHistory, UserTariff
from sqlalchemy import select
from sqlalchemy.orm import selectinload

async def main():
    try:
        async with async_session() as db:
            user_id = 6862350703
            # Tariff query
            tariff_res = await db.execute(
                select(UserTariff)
                .options(selectinload(UserTariff.tariff))
                .where(UserTariff.user_id == user_id)
                .order_by(UserTariff.expires_at.desc())
                .limit(1)
            )
            user_tariff = tariff_res.scalars().first()
            print("Tariff:", user_tariff)
            
            # History query
            downloads_res = await db.execute(
                select(DownloadHistory).where(DownloadHistory.user_id == user_id)
            )
            downloads = downloads_res.scalars().all()
            used_bytes = sum([(d.file_size_bytes or 0) for d in downloads])
            print("Used bytes:", used_bytes)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
