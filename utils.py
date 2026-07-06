"""
============================================================
 utils.py — Yordamchi Funksiyalar va Konstantalar
============================================================
"""

import re
import logging
from dataclasses import dataclass
from enum import Enum, auto

# ─── Logger sozlamasi ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─── Havolani tahlil qilish ──────────────────────────────────────────────────

# Yopiq kanal havolasi formati:
#   https://t.me/c/1234567890/456
#   https://t.me/c/1234567890/456?single  (albom havolasi)
_PRIVATE_LINK_RE = re.compile(
    r"https?://t\.me/c/(?P<chat_id>\d+)/(?P<msg_id>\d+)"
)

# Ochiq kanal/username havolasi:
#   https://t.me/durov/100
_PUBLIC_LINK_RE = re.compile(
    r"https?://t\.me/(?P<username>[a-zA-Z][a-zA-Z0-9_]{3,})/(?P<msg_id>\d+)"
)


@dataclass(frozen=True)
class ParsedLink:
    """Parse qilingan Telegram havola ma'lumotlari."""
    chat_id: int          # To'liq Pyrogram-kompatibel chat ID (-100xxxx yoki @username)
    message_id: int
    is_private: bool      # True → yopiq kanal, False → ochiq kanal/username


def parse_telegram_link(url: str) -> ParsedLink | None:
    """
    Telegram kanal xabar havolasini tahlil qilib ParsedLink qaytaradi.

    Qo'llab-quvvatlanadigan formatlar:
      - https://t.me/c/1234567890/456     (yopiq kanal)
      - https://t.me/durov/100            (ochiq kanal, username orqali)

    Args:
        url: Foydalanuvchi yuborgan havola matni.

    Returns:
        ParsedLink yoki None (agar format noto'g'ri bo'lsa).
    """
    url = url.strip()

    # Yopiq kanal havolasini tekshirish
    m = _PRIVATE_LINK_RE.search(url)
    if m:
        raw_chat_id = int(m.group("chat_id"))
        # Pyrogram yopiq kanallar uchun -100 prefiksini talab qiladi
        full_chat_id = int(f"-100{raw_chat_id}")
        return ParsedLink(
            chat_id=full_chat_id,
            message_id=int(m.group("msg_id")),
            is_private=True,
        )

    # Ochiq kanal havolasini tekshirish
    m = _PUBLIC_LINK_RE.search(url)
    if m:
        username = m.group("username")
        # @BotFather kabi reserved username larni chiqarib tashlash
        if username.lower() in {"joinchat", "addstickers", "c"}:
            return None
        return ParsedLink(
            chat_id=username,       # type: ignore[arg-type]
            message_id=int(m.group("msg_id")),
            is_private=False,
        )

    return None


# ─── Media turi ─────────────────────────────────────────────────────────────

class MediaType(Enum):
    PHOTO    = auto()
    VIDEO    = auto()
    AUDIO    = auto()
    DOCUMENT = auto()
    VOICE    = auto()
    VIDEO_NOTE = auto()
    UNKNOWN  = auto()


def get_media_type(message) -> MediaType:
    """Pyrogram Message ob'ektidan media turini aniqlaydi."""
    if message.photo:        return MediaType.PHOTO
    if message.video:        return MediaType.VIDEO
    if message.audio:        return MediaType.AUDIO
    if message.document:     return MediaType.DOCUMENT
    if message.voice:        return MediaType.VOICE
    if message.video_note:   return MediaType.VIDEO_NOTE
    return MediaType.UNKNOWN


def has_media(message) -> bool:
    """Xabar media mavjudligini tekshiradi."""
    return get_media_type(message) is not MediaType.UNKNOWN


def human_readable_size(size_bytes: int) -> str:
    """Baytni odam o'qiy oladigan formatga o'tkazadi."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"
