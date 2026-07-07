from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional
from database import async_session, UserSession, Proxy, Tariff, User, BotSettings
from sqlalchemy import select
from web.auth import get_current_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])

# --- SESSIONS ---
class SessionOut(BaseModel):
    id: int
    user_id: int
    phone_number: str
    is_active: bool
    stealth_mode: bool
    proxy_id: Optional[int]

@router.get("/sessions", response_model=List[SessionOut])
async def get_all_sessions():
    async with async_session() as db:
        result = await db.execute(select(UserSession))
        sessions = result.scalars().all()
        return sessions

@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int):
    async with async_session() as db:
        session = await db.get(UserSession, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        await db.delete(session)
        await db.commit()
        return {"status": "success"}

@router.post("/users/{user_id}/stealth")
async def toggle_stealth_mode(user_id: int):
    async with async_session() as db:
        result = await db.execute(select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True))
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Faol sessiya topilmadi")
        
        session.stealth_mode = not session.stealth_mode
        await db.commit()
        
        return {"status": "success", "stealth_mode": session.stealth_mode}

# --- PROXIES ---
class ProxyIn(BaseModel):
    scheme: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

class ProxyOut(ProxyIn):
    id: int
    is_active: bool
    fail_count: int

@router.get("/proxies", response_model=List[ProxyOut])
async def get_all_proxies():
    async with async_session() as db:
        result = await db.execute(select(Proxy))
        proxies = result.scalars().all()
        return proxies

@router.post("/proxies", response_model=ProxyOut)
async def create_proxy(proxy: ProxyIn):
    async with async_session() as db:
        new_proxy = Proxy(**proxy.dict())
        db.add(new_proxy)
        await db.commit()
        await db.refresh(new_proxy)
        return new_proxy

@router.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: int):
    async with async_session() as db:
        proxy = await db.get(Proxy, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="Proxy not found")
        await db.delete(proxy)
        await db.commit()
        return {"status": "success"}

# --- TARIFFS ---
class TariffIn(BaseModel):
    name: str
    price: float
    max_file_size_bytes: int
    max_downloads_per_day: int
    duration_days: int

class TariffOut(TariffIn):
    id: int

@router.get("/tariffs", response_model=List[TariffOut])
async def get_all_tariffs():
    async with async_session() as db:
        result = await db.execute(select(Tariff))
        tariffs = result.scalars().all()
        return tariffs

@router.post("/tariffs", response_model=TariffOut)
async def create_tariff(tariff: TariffIn):
    async with async_session() as db:
        new_tariff = Tariff(**tariff.dict())
        db.add(new_tariff)
        await db.commit()
        await db.refresh(new_tariff)
        return new_tariff

@router.put("/tariffs/{tariff_id}", response_model=TariffOut)
async def update_tariff(tariff_id: int, tariff: TariffIn):
    async with async_session() as db:
        existing_tariff = await db.get(Tariff, tariff_id)
        if not existing_tariff:
            raise HTTPException(status_code=404, detail="Tariff not found")
        
        existing_tariff.name = tariff.name
        existing_tariff.price = tariff.price
        existing_tariff.max_file_size_bytes = tariff.max_file_size_bytes
        existing_tariff.max_downloads_per_day = tariff.max_downloads_per_day
        existing_tariff.duration_days = tariff.duration_days
        
        await db.commit()
        await db.refresh(existing_tariff)
        return existing_tariff

@router.delete("/tariffs/{tariff_id}")
async def delete_tariff(tariff_id: int):
    async with async_session() as db:
        tariff = await db.get(Tariff, tariff_id)
        if not tariff:
            raise HTTPException(status_code=404, detail="Tariff not found")
        await db.delete(tariff)
        await db.commit()
        return {"status": "success"}

# --- BROADCAST ---
class BroadcastMessage(BaseModel):
    text: str

from fastapi import Form, UploadFile, File

@router.post("/broadcast")
async def send_broadcast(
    text: str = Form(...),
    image: Optional[UploadFile] = File(None)
):
    # This requires importing the bot instance.
    from bot_instance import bot
    import asyncio
    
    # Save image temporarily if exists
    photo_path = None
    if image and image.filename:
        import os
        from uuid import uuid4
        photo_path = f"/tmp/{uuid4()}_{image.filename}"
        with open(photo_path, "wb") as f:
            f.write(await image.read())

    async def _send_task(users, text, photo):
        from aiogram.types import FSInputFile
        for user in users:
            try:
                if photo:
                    await bot.send_photo(chat_id=user.id, photo=FSInputFile(photo), caption=text)
                else:
                    await bot.send_message(chat_id=user.id, text=text)
                await asyncio.sleep(0.1) # Flood wait avoid
            except Exception:
                pass
        
        # Cleanup
        if photo and os.path.exists(photo):
            try:
                os.remove(photo)
            except:
                pass

    async with async_session() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()
        
    # Run in background to avoid blocking API
    import asyncio
    asyncio.create_task(_send_task(users, text, photo_path))
    
    return {"status": "success", "users_count": len(users)}


# --- BOT SETTINGS ---
class SettingsIn(BaseModel):
    card_number: Optional[str] = None
    card_holder: Optional[str] = None
    bank_name: Optional[str] = None
    payment_channel_id: Optional[int] = None
    force_channels: Optional[str] = "[]"

@router.get("/settings")
async def get_bot_settings():
    async with async_session() as db:
        result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = result.scalar_one_or_none()
        if not settings:
            return {"card_number": None, "card_holder": None, "bank_name": None, "payment_channel_id": None, "force_channels": "[]"}
        return {
            "card_number": settings.card_number,
            "card_holder": settings.card_holder,
            "bank_name": settings.bank_name,
            "payment_channel_id": settings.payment_channel_id,
            "force_channels": settings.force_channels
        }

@router.post("/settings")
async def save_bot_settings(data: SettingsIn):
    async with async_session() as db:
        result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = result.scalar_one_or_none()

        if settings:
            settings.card_number = data.card_number
            settings.card_holder = data.card_holder
            settings.bank_name = data.bank_name
            settings.payment_channel_id = data.payment_channel_id
            settings.force_channels = data.force_channels or "[]"
        else:
            settings = BotSettings(
                id=1,
                card_number=data.card_number,
                card_holder=data.card_holder,
                bank_name=data.bank_name,
                payment_channel_id=data.payment_channel_id,
                force_channels=data.force_channels or "[]"
            )
            db.add(settings)

        await db.commit()
        return {"status": "success", "message": "Sozlamalar saqlandi"}
