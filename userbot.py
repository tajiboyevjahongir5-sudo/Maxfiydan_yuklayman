"""
============================================================
 userbot.py — Pyrogram Userbot Moduli (SaaS)
============================================================
 Bu modul multi-session arxitekturasini ta'minlaydi.
 Har bir foydalanuvchining o'z Pyrogram sessiyasi (Client) bo'ladi.
============================================================
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional, Dict
from sqlalchemy import select

from pyrogram import Client
from pyrogram.errors import (
    AuthKeyUnregistered,
    AuthKeyInvalid,
    AuthKeyDuplicated,
    SessionRevoked,
    Unauthorized,
    ChannelInvalid,
    ChannelPrivate,
    FloodWait,
    MsgIdInvalid,
    PeerIdInvalid,
    UserNotParticipant,
    RPCError,
)
from pyrogram.types import Message as PyroMessage

from config import config
from utils import ParsedLink, has_media, get_media_type, MediaType, human_readable_size
from database import async_session, UserSession

logger = logging.getLogger(__name__)


class UserbotError(Exception):
    """Userbot operatsiyalari uchun maxsus xato sinfi."""
    pass


class SessionRevokedError(UserbotError):
    """Sessiya Telegram tomonidan bekor qilinganda yoki eskirganda otiladigan xato."""
    pass


class MediaTooLargeError(UserbotError):
    pass


class NoMediaError(UserbotError):
    pass


class AccessDeniedError(UserbotError):
    pass


class MessageNotFoundError(UserbotError):
    pass


class SessionManager:
    """
    Multi-tenant Pyrogram sessiyalarini boshqaruvchi klass.
    Har bir user_id o'zining Pyrogram Client'iga ega bo'ladi.
    """

    def __init__(self):
        self.clients: Dict[int, Client] = {}
        self._user_locks: Dict[int, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def start_all(self) -> None:
        """Ma'lumotlar bazasidan barcha faol sessiyalarni yuklaydi va parallel xavfsiz ishga tushiradi."""
        logger.info("🤖 SessionManager: Faol sessiyalar ishga tushirilmoqda...")
        try:
            async with async_session() as db:
                result = await db.execute(select(UserSession).where(UserSession.is_active == True))
                sessions = result.scalars().all()
        except Exception as e:
            logger.error(f"Sessiyalarni bazadan o'qishda xato: {e}")
            return

        async def _start_one(s):
            try:
                await asyncio.wait_for(self.start_session(s.user_id, s.session_string), timeout=12.0)
            except Exception as e:
                logger.error(f"Sessiya (User ID: {s.user_id}) ishga tushmadi: {e}")

        if sessions:
            await asyncio.gather(*[_start_one(s) for s in sessions], return_exceptions=True)

    async def start_session(self, user_id: int, session_string: str) -> None:
        """Yagona foydalanuvchi sessiyasini xavfsiz (per-user lock bilan) ishga tushiradi."""
        user_lock = self._get_user_lock(user_id)
        async with user_lock:
            if user_id in self.clients and self.clients[user_id].is_connected:
                return

            client = Client(
                name=f"session_{user_id}",
                api_id=config.userbot.api_id,
                api_hash=config.userbot.api_hash,
                session_string=session_string,
                in_memory=True,
                max_concurrent_transmissions=8,
                workers=16,
            )

            try:
                # MTProto ulanishini 8 soniyalik timeout bilan bajaramiz (qotib qolmasligi uchun)
                await asyncio.wait_for(client.start(), timeout=8.0)
                self.clients[user_id] = client
                me = await asyncio.wait_for(client.get_me(), timeout=4.0)
            except (AuthKeyUnregistered, AuthKeyInvalid, AuthKeyDuplicated, SessionRevoked, Unauthorized) as e:
                logger.warning(f"⚠️ User ID {user_id} sessiyasi bekor qilingan (start paytida): {e}")
                await self._do_remove_invalid_session(user_id, client)
                return
            except Exception as e:
                err_msg = str(e).lower()
                if any(k in err_msg for k in ["key is not registered", "auth_key_unregistered", "session_revoked", "session_expired", "user_deactivated"]):
                    logger.warning(f"⚠️ User ID {user_id} sessiyasi bekor qilingan (start paytida): {e}")
                    await self._do_remove_invalid_session(user_id, client)
                    return
                # Agar timeout yoki boshqa ulanish xatosi bo'lsa, resurslarni tozalaymiz
                try:
                    if client.is_connected:
                        await client.stop()
                except Exception:
                    pass
                raise e

            logger.info(f"✅ Userbot (ID: {user_id}) ulandi: @{me.username or me.first_name}")

    async def _do_remove_invalid_session(self, user_id: int, client: Optional[Client] = None) -> None:
        """Bekor qilingan/yaroqsiz sessiyani to'xtatadi va bazada faolsizlantiradi (Ichki chaqiruv)."""
        if not client:
            client = self.clients.pop(user_id, None)
        else:
            self.clients.pop(user_id, None)

        if client:
            try:
                if client.is_connected:
                    await client.stop()
            except Exception:
                pass

        try:
            async with async_session() as db:
                result = await db.execute(
                    select(UserSession).where(UserSession.user_id == user_id)
                )
                sessions = result.scalars().all()
                for s in sessions:
                    s.is_active = False
                await db.commit()
            logger.warning(f"⚠️ Sessiya bekor qilindi va faolsizlantirildi (User ID: {user_id})")
        except Exception as e:
            logger.error(f"Sessiyani faolsizlantirishda DB xatosi: {e}")

    async def remove_invalid_session(self, user_id: int) -> None:
        """Bekor qilingan/yaroqsiz sessiyani to'xtatadi va bazada faolsizlantiradi."""
        user_lock = self._get_user_lock(user_id)
        async with user_lock:
            await self._do_remove_invalid_session(user_id)

    async def stop_all(self) -> None:
        """Barcha ochiq sessiyalarni to'xtatadi."""
        async with self._global_lock:
            for user_id, client in list(self.clients.items()):
                if client.is_connected:
                    try:
                        await client.stop()
                    except Exception:
                        pass
                    logger.info(f"🛑 Userbot (ID: {user_id}) to'xtatildi.")
            self.clients.clear()

    def get_client(self, user_id: int) -> Client:
        """Berilgan user_id uchun Client qaytaradi."""
        if user_id not in self.clients or not self.clients[user_id].is_connected:
            raise UserbotError(
                "Sizning Telegram profilingiz (sessiyangiz) tizimga ulanmagan. "
                "Iltimos, avval /admin komandasi orqali Web Dashboard'ga kiring va profilingizni ulang."
            )
        return self.clients[user_id]

    async def fetch_and_download(self, user_id: int, parsed_link: ParsedLink, progress_callback=None):
        """
        Maxsus user_id sessiyasi yordamida medialni yuklab oladi.
        List of (path, media_type) tuples qaytaradi (Albomlar uchun).
        """
        try:
            client = self.get_client(user_id)
            messages = await self._get_messages(client, parsed_link, user_id)
            
            results = []
            total_files = len(messages)
            
            for i, message in enumerate(messages):
                media_type = get_media_type(message)
                
                async def wrapped_progress(current, total, *args, **kwargs):
                    if progress_callback:
                        await progress_callback(current, total, current_file=i+1, total_files=total_files)

                file_path = await self._download_media(client, message, user_id, wrapped_progress)
                if file_path:
                    results.append((file_path, media_type))
                    
            return results
        except (AuthKeyUnregistered, AuthKeyInvalid, AuthKeyDuplicated, SessionRevoked, Unauthorized) as e:
            await self.remove_invalid_session(user_id)
            raise SessionRevokedError(
                "⚠️ Sizning Telegram sessiyangiz bekor qilingan/uzilgan!\n\n"
                "Iltimos, Web App -> Ulanish bo'limidan Telegram akkauntingizni qayta ulang."
            ) from e

    async def _get_messages(self, client: Client, parsed_link: ParsedLink, user_id: int) -> list[PyroMessage]:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                messages = await client.get_messages(
                    chat_id=parsed_link.chat_id,
                    message_ids=parsed_link.message_id,
                )

                message: PyroMessage = (
                    messages if isinstance(messages, PyroMessage) else messages[0]
                ) if messages else None

                if not message or message.empty:
                    raise MessageNotFoundError("Xabar topilmadi yoki o'chirilgan.")
                if not has_media(message):
                    raise NoMediaError("Xabarda yuklanadigan media yo'q.")

                # Agar media guruhi bo'lsa, barchasini yuklash
                if message.media_group_id:
                    try:
                        media_group = await client.get_media_group(
                            chat_id=parsed_link.chat_id,
                            message_id=parsed_link.message_id
                        )
                        filtered_group = [m for m in media_group if has_media(m)]
                        if filtered_group:
                            return filtered_group
                    except Exception as e:
                        logger.warning(f"get_media_group xatosi (single fallback qo'llaniladi): {e}")

                return [message]

            except (AuthKeyUnregistered, AuthKeyInvalid, AuthKeyDuplicated, SessionRevoked, Unauthorized) as e:
                await self.remove_invalid_session(user_id)
                raise SessionRevokedError(
                    "⚠️ Sizning Telegram sessiyangiz bekor qilingan/uzilgan!\n\n"
                    "Iltimos, Web App -> Ulanish bo'limidan Telegram akkauntingizni qayta ulang."
                ) from e
            except FloodWait as e:
                wait_seconds = e.value
                if attempt == max_retries:
                    raise UserbotError(f"Cheklov: {wait_seconds} soniya kuting.") from e
                await asyncio.sleep(wait_seconds + 1)
            except (ChannelInvalid, ChannelPrivate, PeerIdInvalid) as e:
                raise AccessDeniedError("Kanalga kirish taqiqlangan! Akkauntingiz kanalga a'zomi?") from e
            except UserNotParticipant as e:
                raise AccessDeniedError("Siz bu kanalga a'zo emassiz!") from e
            except MsgIdInvalid as e:
                raise MessageNotFoundError("Xabar ID noto'g'ri!") from e
            except RPCError as e:
                err_msg = str(e).lower()
                if any(k in err_msg for k in ["key is not registered", "auth_key_unregistered", "session_revoked", "session_expired", "user_deactivated"]):
                    await self.remove_invalid_session(user_id)
                    raise SessionRevokedError(
                        "⚠️ Sizning Telegram sessiyangiz bekor qilingan/uzilgan!\n\n"
                        "Iltimos, Web App -> Ulanish bo'limidan Telegram akkauntingizni qayta ulang."
                    ) from e
                if attempt == max_retries:
                    raise UserbotError(f"Telegram API xatosi: {e.MESSAGE}") from e
                await asyncio.sleep(2 ** attempt)

    async def _download_media(self, client: Client, message: PyroMessage, user_id: int, progress_callback=None) -> Path:
        media_type = get_media_type(message)
        file_size = self._get_file_size(message, media_type)
        
        unique_prefix = uuid.uuid4().hex[:8]
        dest_path = config.download_dir / f"{unique_prefix}_{message.id}"

        try:
            downloaded_path = await client.download_media(
                message=message,
                file_name=str(dest_path),
                progress=progress_callback
            )
        except (AuthKeyUnregistered, AuthKeyInvalid, AuthKeyDuplicated, SessionRevoked, Unauthorized) as e:
            await self.remove_invalid_session(user_id)
            raise SessionRevokedError(
                "⚠️ Sizning Telegram sessiyangiz bekor qilingan/uzilgan!\n\n"
                "Iltimos, Web App -> Ulanish bo'limidan Telegram akkauntingizni qayta ulang."
            ) from e
        except FloodWait as e:
            raise UserbotError(f"Yuklash cheklandi. {e.value} s kuting.") from e
        except RPCError as e:
            err_msg = str(e).lower()
            if any(k in err_msg for k in ["key is not registered", "auth_key_unregistered", "session_revoked", "session_expired", "user_deactivated"]):
                await self.remove_invalid_session(user_id)
                raise SessionRevokedError(
                    "⚠️ Sizning Telegram sessiyangiz bekor qilingan/uzilgan!\n\n"
                    "Iltimos, Web App -> Ulanish bo'limidan Telegram akkauntingizni qayta ulang."
                ) from e
            raise UserbotError(f"Media yuklab olinmadi: {e.MESSAGE}") from e

        if not downloaded_path:
            raise UserbotError("Noma'lum xato yuz berdi.")

        result_path = Path(str(downloaded_path))
        return result_path

    @staticmethod
    def _get_file_size(message: PyroMessage, media_type: MediaType) -> Optional[int]:
        size_map = {
            MediaType.VIDEO:    lambda: message.video.file_size if message.video else None,
            MediaType.AUDIO:    lambda: message.audio.file_size if message.audio else None,
            MediaType.DOCUMENT: lambda: message.document.file_size if message.document else None,
            MediaType.VOICE:    lambda: message.voice.file_size if message.voice else None,
            MediaType.VIDEO_NOTE: lambda: message.video_note.file_size if message.video_note else None,
            MediaType.PHOTO:    lambda: None,
        }
        getter = size_map.get(media_type)
        return getter() if getter else None


# Global session manager instance
userbot = SessionManager()
