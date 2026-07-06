"""
============================================================
 config.py — Markaziy Konfiguratsiya Moduli
============================================================
 Barcha muhit o'zgaruvchilari shu yerdan o'qiladi va
 tipga o'tkaziladi. Noto'g'ri config dastur boshlanishida
 xato beradi (fail-fast prinsipi).
============================================================
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# .env faylini yuklash
load_dotenv()


@dataclass(frozen=True)
class BotConfig:
    """Aiogram bot konfiguratsiyasi."""
    token: str

    def __post_init__(self):
        if not self.token or ":" not in self.token:
            raise ValueError(
                "❌ BOT_TOKEN noto'g'ri yoki bo'sh. "
                ".env faylini tekshiring."
            )


@dataclass(frozen=True)
class UserbotConfig:
    """Pyrogram userbot konfiguratsiyasi."""
    api_id: int
    api_hash: str
    session_string: str

    def __post_init__(self):
        if self.api_id == 0:
            raise ValueError(
                "❌ API_ID noto'g'ri. "
                "https://my.telegram.org/apps dan oling."
            )
        if not self.api_hash:
            raise ValueError(
                "❌ API_HASH bo'sh. "
                "https://my.telegram.org/apps dan oling."
            )
        if not self.session_string:
            raise ValueError(
                "❌ PYROGRAM_SESSION_STRING bo'sh. "
                "'python generate_session.py' ni ishga tushiring."
            )


@dataclass(frozen=True)
class AppConfig:
    """Umumiy dastur konfiguratsiyasi."""
    bot: BotConfig
    userbot: UserbotConfig
    download_dir: Path
    max_file_size_bytes: int
    database_url: str
    admin_id: int
    allowed_users: set[int] = field(default_factory=set)

    def __post_init__(self):
        # Download papkasini yaratish (mavjud bo'lmasa)
        self.download_dir.mkdir(parents=True, exist_ok=True)


def _parse_allowed_users(raw: str) -> set[int]:
    """Vergul bilan ajratilgan foydalanuvchi ID larini parse qiladi."""
    if not raw or not raw.strip():
        return set()
    result = set()
    for uid in raw.split(","):
        uid = uid.strip()
        if uid.isdigit():
            result.add(int(uid))
    return result


def load_config() -> AppConfig:
    """
    Muhit o'zgaruvchilaridan konfiguratsiya ob'ektini yaratadi.
    
    Returns:
        AppConfig: Validatsiyadan o'tgan konfiguratsiya.
    
    Raises:
        ValueError: Biron-bir majburiy parametr to'g'ri bo'lmasa.
    """
    max_mb = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))
    
    # Agar DATABASE_URL ko'rsatilmagan bo'lsa, lokal SQLite ishlatiladi
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///storage.db")
    # Railway'ning postgres:// formatini postgresql+asyncpg:// formatiga o'zgartirish
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        
    return AppConfig(
        bot=BotConfig(
            token=os.getenv("BOT_TOKEN", ""),
        ),
        userbot=UserbotConfig(
            api_id=int(os.getenv("API_ID", "0")),
            api_hash=os.getenv("API_HASH", ""),
            session_string=os.getenv("PYROGRAM_SESSION_STRING", ""),
        ),
        download_dir=Path(os.getenv("DOWNLOAD_DIR", "./downloads")),
        max_file_size_bytes=max_mb * 1024 * 1024,  # MB → Bayt
        database_url=db_url,
        admin_id=int(os.getenv("ADMIN_ID", "0")),
        allowed_users=_parse_allowed_users(
            os.getenv("ALLOWED_USERS", "")
        ),
    )


# Global konfiguratsiya ob'ekti (modul import bo'lganida bir marta yuklanadi)
config: AppConfig = load_config()
