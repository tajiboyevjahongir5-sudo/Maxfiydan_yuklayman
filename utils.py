"""
============================================================
 utils.py — Yordamchi Funksiyalar va Konstantalar
============================================================
"""

import os
import re
import struct
import logging
from typing import Union, Optional
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


def parse_target_chat(val: Union[int, str]) -> Union[int, str]:
    """Kanal yoki guruh identifikatorini (link, username, int) to'g'ri Pyrogram formatiga o'tkazadi."""
    if isinstance(val, int):
        return val
    s = str(val).strip()
    if not s:
        return s
    # 1. Yopiq kanal/guruh havolasi: t.me/c/1234567890/123 yoki t.me/c/1234567890
    m = re.search(r"t\.me/c/(\d+)", s)
    if m:
        return int(f"-100{m.group(1)}")
    # 2. Ochiq kanal/guruh havolasi: t.me/username
    m_pub = re.search(r"t\.me/([a-zA-Z0-9_]{3,})", s)
    if m_pub:
        uname = m_pub.group(1)
        if uname.lower() not in {"joinchat", "addstickers", "c"}:
            return uname
    # 3. @username
    if s.startswith("@"):
        return s.lstrip("@")
    # 4. Raqamli ID: -100... yoki 123456789
    try:
        num = int(s)
        if num > 0 and len(s) >= 9:
            return int(f"-100{num}")
        return num
    except ValueError:
        return s


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


def extract_mp4_metadata(file_path: Union[str, os.PathLike]) -> tuple[int, int, int]:
    """
    MP4/MOV/M4V fayl sarlavhasidan (moov/tkhd/mvhd) asl kenglik, balandlik va davomiylikni o'qiydi.
    Qaytaradi: (width, height, duration)
    """
    width, height, duration = 0, 0, 0
    try:
        p = str(file_path)
        if not os.path.exists(p) or os.path.getsize(p) < 32:
            return 0, 0, 0

        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_len = f.tell()
            f.seek(0)

            while f.tell() < file_len:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                size, atom_type = struct.unpack(">I4s", hdr)
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0] - 8
                    header_len = 16
                else:
                    header_len = 8

                if atom_type == b"moov":
                    moov_data = f.read(size - header_len)

                    # 1. mvhd orqali davomiylikni olish
                    mvhd_idx = moov_data.find(b"mvhd")
                    if mvhd_idx != -1:
                        try:
                            v = moov_data[mvhd_idx + 4]
                            if v == 1:
                                ts_offset = mvhd_idx + 4 + 4 + 16
                                timescale, dur = struct.unpack(">IQ", moov_data[ts_offset:ts_offset + 12])
                            else:
                                ts_offset = mvhd_idx + 4 + 4 + 8
                                timescale, dur = struct.unpack(">II", moov_data[ts_offset:ts_offset + 8])
                            if timescale > 0:
                                duration = int(dur / timescale)
                        except Exception:
                            pass

                    # 2. tkhd orqali o'lchamlarni olish
                    tkhd_idx = 0
                    while True:
                        tkhd_idx = moov_data.find(b"tkhd", tkhd_idx)
                        if tkhd_idx == -1:
                            break
                        try:
                            v = moov_data[tkhd_idx + 4]
                            offset = tkhd_idx + 4 + (88 if v == 1 else 76)
                            w_raw, h_raw = struct.unpack(">II", moov_data[offset:offset + 8])
                            w = w_raw >> 16
                            h = h_raw >> 16
                            if w > 0 and h > 0:
                                width, height = w, h
                                break
                        except Exception:
                            pass
                        tkhd_idx += 4
                    break
                else:
                    if size <= header_len:
                        break
                    f.seek(size - header_len, os.SEEK_CUR)
    except Exception:
        pass
    return width, height, duration
