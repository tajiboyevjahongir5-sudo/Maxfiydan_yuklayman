import os
import logging
import secrets
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# JWT imzolash kaliti. ADMIN_SECRET .env orqali berilishi SHART.
# Agar berilmagan bo'lsa — har bir ishga tushishda tasodifiy kalit yaratiladi
# (token'larni soxtalashtirib bo'lmaydi, lekin qayta ishga tushganda admin
#  qaytadan login qilishi kerak bo'ladi). Hech qachon qat'iy default kalit
# ishlatilmaydi, aks holda istalgan kishi admin token yasab olishi mumkin.
_DEPRECATED_DEFAULT = "default_secret_key_change_me"
SECRET_KEY = os.getenv("ADMIN_SECRET", "")
if not SECRET_KEY or SECRET_KEY == _DEPRECATED_DEFAULT:
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning(
        "⚠️ ADMIN_SECRET o'rnatilmagan! Vaqtinchalik tasodifiy kalit yaratildi. "
        "Ishlab chiqarish (production) uchun .env ga kuchli ADMIN_SECRET qo'shing."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str = None

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_admin(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str: str = payload.get("sub")
        from config import config
        if user_id_str is None or int(user_id_str) not in config.admin_ids:
            raise credentials_exception
        token_data = TokenData(username=user_id_str)
    except JWTError:
        raise credentials_exception
    return token_data

async def get_current_user_id(token: str = Depends(oauth2_scheme)) -> int:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        return int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception
