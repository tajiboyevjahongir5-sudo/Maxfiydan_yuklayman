import asyncio
import logging
from pyrogram import Client
from pyrogram.enums import MessageMediaType, MessagesFilter
from pyrogram.errors import FloodWait
import os
from config import config

logger = logging.getLogger(__name__)

# Global xotira (Har bir foydalanuvchi uchun 1 ta jarayon)
transfer_states = {}

async def run_transfer(user_id: int, client: Client, source_chat_id: int, target_chat_id: int, media_type: str):
    logger.info(f"Ko'chirish boshlandi (User {user_id}): {source_chat_id} -> {target_chat_id} ({media_type})")
    
    transfer_states[user_id] = {"total": 0, "current": 0, "status": "initializing", "message": "Tayyorlanmoqda..."}
    
    try:
        # Filtrni tanlash
        if media_type == "photo":
            filter_type = MessagesFilter.PHOTO
        elif media_type == "video":
            filter_type = MessagesFilter.VIDEO
        else:
            filter_type = MessagesFilter.PHOTO_VIDEO
            
        # Umumiy sonini topish
        total_count = await client.search_messages_count(chat_id=source_chat_id, filter=filter_type)
        if total_count == 0:
            transfer_states[user_id]["status"] = "completed"
            transfer_states[user_id]["message"] = "Ko'chirish uchun hech qanday fayl topilmadi!"
            return

        transfer_states[user_id] = {"total": total_count, "current": 0, "status": "running", "message": "Jarayonda..."}
        
        # Eskidan yangiga qarab ko'chirish uchun avval xabarlarni yig'ib olish qiyin bo'ladi (agarda ular juda ko'p bo'lsa xotirani to'ldiradi).
        # Shuning uchun eng yangisidan boshlab ko'chiraveramiz yoki limit bilan teskari qilamiz.
        # Bu yerda limitni xavfsizlik uchun qisqartirmaymiz. Tizim search_messages iteratorini ishlatadi.
        
        count = 0
        messages_to_copy = []
        async for message in client.search_messages(chat_id=source_chat_id, filter=filter_type):
            messages_to_copy.append(message)
            # Agarda 200 tadan oshib ketsa, qolganini o'tkazmaymiz (test uchun va xotirani to'ldirmaslik uchun, lekin qoldirmasdan deyishgan). 
            # Mayli barchasini yig'amiz! (Pyrogram limitni o'zi boshqaradi)
            
        # Eski xabarlar birinchi bo'lishi uchun teskari qilamiz:
        messages_to_copy.reverse()
        
        # Real-time total count ni qayta saqlaymiz (aniqroq):
        total_count = len(messages_to_copy)
        transfer_states[user_id]["total"] = total_count

        for message in messages_to_copy:
            try:
                # To'g'ridan-to'g'ri ko'chirishga harakat qilamiz
                await client.copy_message(
                    chat_id=target_chat_id,
                    from_chat_id=source_chat_id,
                    message_id=message.id,
                    caption=message.caption
                )
            except Exception as e:
                err_str = str(e).upper()
                if "RESTRICTED" in err_str or "INVALID" in err_str:
                    logger.info(f"Yopiq kanal, yuklab olinmoqda (msg_id: {message.id})...")
                    file_path = await client.download_media(message, file_name=str(config.download_dir) + "/")
                    if file_path:
                        try:
                            if message.media == MessageMediaType.PHOTO:
                                await client.send_photo(target_chat_id, photo=file_path, caption=message.caption)
                            elif message.media == MessageMediaType.VIDEO:
                                await client.send_video(target_chat_id, video=file_path, caption=message.caption)
                            else:
                                await client.send_document(target_chat_id, document=file_path, caption=message.caption)
                        finally:
                            if os.path.exists(file_path):
                                os.remove(file_path)
                else:
                    logger.error(f"Copy failed msg_id {message.id}: {e}")
                    
            count += 1
            transfer_states[user_id]["current"] = count
            
            # Telegram rate limitdan saqlanish (har xabar uchun 1.5 soniya)
            await asyncio.sleep(1.5)
            
        transfer_states[user_id]["status"] = "completed"
        transfer_states[user_id]["message"] = f"Muvaffaqiyatli yakunlandi! ({count} ta media ko'chirildi)"
        logger.info(f"Ko'chirish tugadi (User {user_id}). {count} ta media.")
        
    except FloodWait as e:
        transfer_states[user_id]["status"] = "error"
        transfer_states[user_id]["message"] = f"Telegram cheklovi: {e.value} soniya kuting."
    except Exception as e:
        logger.error(f"Transfer error: {e}")
        transfer_states[user_id]["status"] = "error"
        transfer_states[user_id]["message"] = f"Xatolik: {e}"
