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
            
        # Get active tariff
        tariff_res = await db.execute(
            select(UserTariff)
            .options(selectinload(UserTariff.tariff)) # relationship
            .where(UserTariff.user_id == user_id)
        )
        user_tariff = tariff_res.scalar_one_or_none()
        
        # Get total downloaded today/month (mocking total gb for now since we just track size)
        downloads_res = await db.execute(
            select(DownloadHistory).where(DownloadHistory.user_id == user_id)
        )
        downloads = downloads_res.scalars().all()
        used_bytes = sum([d.file_size_bytes for d in downloads])
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
