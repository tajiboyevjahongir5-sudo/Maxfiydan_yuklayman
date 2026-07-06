import hmac
import hashlib
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from config import config
from database import async_session, User
from sqlalchemy import select
from web.auth import create_access_token
from datetime import timedelta

router = APIRouter(prefix="/api/auth", tags=["auth"])

class TelegramAuthData(BaseModel):
    id: int
    first_name: str
    username: str = None
    photo_url: str = None
    auth_date: int
    hash: str

def check_telegram_authorization(data: TelegramAuthData) -> bool:
    """Telegram login ma'lumotlarini bot token orqali tasdiqlash."""
    data_dict = data.dict(exclude={"hash"})
    # None qiymatlarni olib tashlash
    data_dict = {k: v for k, v in data_dict.items() if v is not None}
    
    data_check_string = '\n'.join([f"{k}={v}" for k, v in sorted(data_dict.items())])
    secret_key = hashlib.sha256(config.bot.token.encode()).digest()
    hash_calc = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    return hash_calc == data.hash

@router.post("/telegram")
async def telegram_login(data: TelegramAuthData):
    """Telegram Login Widget dan kelgan auth datani tekshiradi."""
    if not check_telegram_authorization(data):
        raise HTTPException(status_code=401, detail="Data is NOT from Telegram")

    # Ma'lumotlar bazasida user ni izlash/yaratish
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == data.id))
        user = result.scalar_one_or_none()
        
        if not user:
            user = User(
                id=data.id,
                first_name=data.first_name,
                username=data.username,
            )
            db.add(user)
            await db.commit()
            
    # JWT token yaratish
    access_token = create_access_token(
        data={"sub": str(data.id)},
        expires_delta=timedelta(days=7)
    )
    
    return {"access_token": access_token, "token_type": "bearer"}
