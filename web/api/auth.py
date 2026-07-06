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

from urllib.parse import parse_qsl

class TelegramAuthData(BaseModel):
    initData: str = None
    id: int = None
    first_name: str = None
    username: str = None
    photo_url: str = None
    auth_date: int = None
    hash: str = None

def check_telegram_authorization(init_data: str) -> dict:
    """Telegram WebApp initData ni tasdiqlash va user ma'lumotlarini qaytarish."""
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None
            
        hash_val = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", config.bot.token.encode(), hashlib.sha256).digest()
        hash_calc = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if hash_calc == hash_val:
            import json
            return json.loads(parsed_data.get("user", "{}"))
        return None
    except Exception:
        return None

def check_telegram_widget_auth(data: TelegramAuthData) -> bool:
    data_dict = data.dict(exclude={"initData", "hash"})
    data_dict = {k: v for k, v in data_dict.items() if v is not None}
    data_check_string = "\n".join([f"{k}={v}" for k, v in sorted(data_dict.items())])
    secret_key = hashlib.sha256(config.bot.token.encode()).digest()
    hash_calc = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hash_calc == data.hash

@router.post("/telegram")
async def telegram_login(data: TelegramAuthData):
    """Telegram WebApp yoki Login Widget dan kelgan auth datani tekshiradi."""
    
    if data.initData:
        user_data = check_telegram_authorization(data.initData)
        if not user_data or "id" not in user_data:
            raise HTTPException(status_code=401, detail="Avtorizatsiya xatosi (Invalid initData)")
        user_id = user_data["id"]
        first_name = user_data.get("first_name", "")
        username = user_data.get("username")
    else:
        if not data.hash or not check_telegram_widget_auth(data):
            raise HTTPException(status_code=401, detail="Data is NOT from Telegram Widget")
        user_id = data.id
        first_name = data.first_name or ""
        username = data.username

    # Ma'lumotlar bazasida user ni izlash/yaratish
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            user = User(
                id=user_id,
                first_name=first_name,
                username=username,
            )
            db.add(user)
            await db.commit()
            
    # JWT token yaratish
    access_token = create_access_token(
        data={"sub": str(user_id)},
        expires_delta=timedelta(days=7)
    )
    
    return {"access_token": access_token, "token_type": "bearer"}
