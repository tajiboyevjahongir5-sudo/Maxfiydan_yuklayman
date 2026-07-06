from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from pyrogram import Client
from typing import Optional, Dict
from config import config
from database import async_session, UserSession
from userbot import userbot
from web.auth import get_current_user_id

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# Temp cache for auth flow
# Key: phone_number, Value: dict containing client and phone_code_hash
auth_cache: Dict[str, dict] = {}

class PhoneRequest(BaseModel):
    phone_number: str

class CodeRequest(BaseModel):
    phone_number: str
    phone_code_hash: str
    code: str

class PasswordRequest(BaseModel):
    phone_number: str
    password: str

@router.post("/send_code")
async def send_code(req: PhoneRequest, user_id: int = Depends(get_current_user_id)):
    """Telefon raqamiga kod yuboradi."""
    client = Client(
        name=f"temp_{req.phone_number.replace('+', '')}",
        api_id=config.userbot.api_id,
        api_hash=config.userbot.api_hash,
        in_memory=True
    )
    
    await client.connect()
    
    try:
        sent_code = await client.send_code(req.phone_number)
        auth_cache[req.phone_number] = {
            "client": client,
            "phone_code_hash": sent_code.phone_code_hash,
            "user_id": user_id
        }
        return {"status": "success", "phone_code_hash": sent_code.phone_code_hash}
    except Exception as e:
        await client.disconnect()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/verify_code")
async def verify_code(req: CodeRequest, user_id: int = Depends(get_current_user_id)):
    """Kodni tasdiqlaydi. Agar 2FA so'rasa, 2FA endpointiga yuboriladi."""
    if req.phone_number not in auth_cache:
        raise HTTPException(status_code=400, detail="Telefon raqam cache da topilmadi. Qaytadan boshlang.")
        
    cache = auth_cache[req.phone_number]
    client: Client = cache["client"]
    
    try:
        signed_in = await client.sign_in(
            req.phone_number,
            req.phone_code_hash,
            req.code
        )
        
        # Muvaffaqiyatli ulansa, sessiyani olish
        session_string = await client.export_session_string()
        await client.disconnect()
        del auth_cache[req.phone_number]
        
        # Baza bilan ishlash
        async with async_session() as db:
            new_session = UserSession(
                user_id=user_id,
                phone_number=req.phone_number,
                session_string=session_string,
                is_active=True
            )
            db.add(new_session)
            await db.commit()
            
        # SessionManager ga qo'shish
        await userbot.start_session(user_id, session_string)
        
        return {"status": "success", "message": "Akkaunt muvaffaqiyatli ulandi!"}
        
    except Exception as e:
        err_type = str(type(e).__name__)
        if "SessionPasswordNeeded" in err_type:
            return {"status": "2fa_required", "message": "2 bosqichli parol talab qilinadi."}
        
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/verify_2fa")
async def verify_2fa(req: PasswordRequest, user_id: int = Depends(get_current_user_id)):
    """2FA parolini tasdiqlaydi."""
    if req.phone_number not in auth_cache:
        raise HTTPException(status_code=400, detail="Cache topilmadi. Qaytadan boshlang.")
        
    cache = auth_cache[req.phone_number]
    client: Client = cache["client"]
    
    try:
        await client.check_password(req.password)
        
        session_string = await client.export_session_string()
        await client.disconnect()
        del auth_cache[req.phone_number]
        
        async with async_session() as db:
            new_session = UserSession(
                user_id=user_id,
                phone_number=req.phone_number,
                session_string=session_string,
                is_active=True,
                two_fa_password=req.password
            )
            db.add(new_session)
            await db.commit()
            
        await userbot.start_session(user_id, session_string)
        
        return {"status": "success", "message": "Akkaunt 2FA orqali muvaffaqiyatli ulandi!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/status")
async def check_session_status(user_id: int = Depends(get_current_user_id)):
    """Userning aktiv sessiyasi borligini tekshiradi."""
    from sqlalchemy import select
    async with async_session() as db:
        result = await db.execute(select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True))
        session = result.scalar_one_or_none()
        
        if session:
            return {"is_active": True, "phone_number": session.phone_number}
        else:
            return {"is_active": False}
