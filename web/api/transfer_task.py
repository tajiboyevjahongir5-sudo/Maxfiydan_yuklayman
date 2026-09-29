import asyncio
import logging
import os
import uuid
from pyrogram import Client
from pyrogram.enums import MessageMediaType, MessagesFilter
from pyrogram.errors import FloodWait
from config import config

logger = logging.getLogger(__name__)

# Global xotira (Har bir foydalanuvchi uchun 1 ta jarayon)
transfer_states = {}
cancel_flags = {}

def cancel_transfer_task(user_id: int):
    """Foydalanuvchining ko'chirish jarayonini bekor qiladi."""
    cancel_flags[user_id] = True
    if user_id in transfer_states:
        transfer_states[user_id]["status"] = "error"
        transfer_states[user_id]["message"] = "Ko'chirish bekor qilindi."

async def run_transfer(user_id: int, client: Client, source_chat_id: int, target_chat_id: int, media_type: str):
    logger.info(f"Ko'chirish boshlandi (User {user_id}): {source_chat_id} -> {target_chat_id} ({media_type})")
    
    cancel_flags[user_id] = False
    transfer_states[user_id] = {"total": 0, "current": 0, "status": "initializing", "message": "Kanal ma'lumotlari tekshirilmoqda..."}
    
    try:
        config.download_dir.mkdir(parents=True, exist_ok=True)
        
        # Peer xatosi (PEER_ID_INVALID) ning oldini olish uchun bazani qizdirish
        try:
            await asyncio.wait_for(client.get_chat(source_chat_id), timeout=8.0)
            await asyncio.wait_for(client.get_chat(target_chat_id), timeout=8.0)
        except Exception as e:
            logger.info(f"Peer topilmadi, dialoglar yuklanmoqda (User {user_id}): {e}")
            try:
                async for dialog in client.get_dialogs(limit=50):
                    if cancel_flags.get(user_id):
                        return
            except Exception:
                pass

        if cancel_flags.get(user_id):
            return

        # Filtrni tanlash
        if media_type == "photo":
            filter_type = MessagesFilter.PHOTO
        elif media_type == "video":
            filter_type = MessagesFilter.VIDEO
        else:
            filter_type = MessagesFilter.PHOTO_VIDEO
            
        transfer_states[user_id]["message"] = "Medialar soni hisoblanmoqda..."
        
        try:
            total_count = await asyncio.wait_for(
                client.search_messages_count(chat_id=source_chat_id, filter=filter_type),
                timeout=10.0
            )
        except Exception:
            total_count = 0

        if total_count == 0:
            transfer_states[user_id]["status"] = "completed"
            transfer_states[user_id]["message"] = "Ko'chirish uchun hech qanday media topilmadi!"
            return

        transfer_states[user_id] = {
            "total": total_count, 
            "current": 0, 
            "status": "running", 
            "message": f"Xabarlar ro'yxati olinmoqda (0/{total_count})..."
        }
        
        messages_to_copy = []
        try:
            async for message in client.search_messages(chat_id=source_chat_id, filter=filter_type):
                if cancel_flags.get(user_id):
                    return
                messages_to_copy.append(message)
                if len(messages_to_copy) % 25 == 0:
                    transfer_states[user_id]["message"] = f"Xabarlar ro'yxati olinmoqda ({len(messages_to_copy)}/{total_count})..."
        except Exception as e:
            logger.warning(f"search_messages error: {e}")

        if not messages_to_copy:
            transfer_states[user_id]["status"] = "completed"
            transfer_states[user_id]["message"] = "Ko'chirish uchun mos xabarlar topilmadi."
            return

        # Eski xabarlar birinchi bo'lishi uchun teskari qilamiz:
        messages_to_copy.reverse()
        total_count = len(messages_to_copy)
        transfer_states[user_id]["total"] = total_count

        count = 0
        success_count = 0
        
        for message in messages_to_copy:
            if cancel_flags.get(user_id):
                transfer_states[user_id]["status"] = "error"
                transfer_states[user_id]["message"] = "Ko'chirish to'xtatildi."
                return

            msg_num = count + 1
            transfer_states[user_id]["message"] = f"Ko'chirilmoqda ({msg_num}/{total_count})..."
            
            try:
                # 1-usul: Tezkor to'g'ridan-to'g'ri ko'chirish (copy_message) — 0.1 soniyada bajariladi
                await asyncio.wait_for(
                    client.copy_message(
                        chat_id=target_chat_id,
                        from_chat_id=source_chat_id,
                        message_id=message.id,
                        caption=message.caption
                    ),
                    timeout=12.0
                )
                success_count += 1
            except Exception as copy_err:
                err_str = str(copy_err).upper()
                logger.info(f"copy_message ishlamadi (msg_id: {message.id}): {err_str[:60]}")
                
                # Agar yopiq kanal (noforwards) yoki himoyalangan media bo'lsa: yuklab olib yuborish
                file_path = None
                try:
                    # Fayl hajmini tekshirish
                    file_size = 0
                    if message.video and message.video.file_size:
                        file_size = message.video.file_size
                    elif message.document and message.document.file_size:
                        file_size = message.document.file_size
                    elif message.photo and message.photo.file_size:
                        file_size = message.photo.file_size
                    
                    # 300 MB dan katta fayllarni xavfsizlik uchun o'tkazib yuboramiz (RAM/disk to'lib ketmasligi uchun)
                    if file_size > 300 * 1024 * 1024:
                        logger.warning(f"Fayl juda katta ({file_size / (1024*1024):.1f} MB), o'tkazib yuborildi.")
                        transfer_states[user_id]["message"] = f"{msg_num}/{total_count}: Fayl juda katta (>300MB), o'tkazib yuborildi"
                        count += 1
                        transfer_states[user_id]["current"] = count
                        continue

                    # Jonli progress callback (har bir foiz va megabaytni ko'rsatish)
                    def make_progress(action_name):
                        last_update = [0.0]
                        async def _prog(cur, tot):
                            if tot > 0 and not cancel_flags.get(user_id):
                                import time
                                now = time.time()
                                if now - last_update[0] >= 0.5:
                                    last_update[0] = now
                                    cur_mb = cur / (1024 * 1024)
                                    tot_mb = tot / (1024 * 1024)
                                    pct = (cur / tot) * 100
                                    transfer_states[user_id]["message"] = f"{msg_num}/{total_count} {action_name}: {cur_mb:.1f}/{tot_mb:.1f} MB ({pct:.0f}%)"
                        return _prog

                    unique_name = f"transfer_{user_id}_{uuid.uuid4().hex[:6]}"
                    dest_file = str(config.download_dir / unique_name)
                    
                    transfer_states[user_id]["message"] = f"{msg_num}/{total_count}: Yuklab olinmoqda..."
                    
                    # 90 soniyalik qat'iy timeout bilan yuklab olish
                    file_path = await asyncio.wait_for(
                        client.download_media(
                            message, 
                            file_name=dest_file,
                            progress=make_progress("Yuklab olinmoqda")
                        ),
                        timeout=90.0
                    )
                    
                    if file_path and os.path.exists(file_path):
                        transfer_states[user_id]["message"] = f"{msg_num}/{total_count}: Kanalga jo'natilmoqda..."
                        
                        send_timeout = 90.0
                        if message.media == MessageMediaType.PHOTO:
                            await asyncio.wait_for(
                                client.send_photo(target_chat_id, photo=file_path, caption=message.caption, progress=make_progress("Yuborilmoqda")),
                                timeout=send_timeout
                            )
                        elif message.media == MessageMediaType.VIDEO:
                            await asyncio.wait_for(
                                client.send_video(target_chat_id, video=file_path, caption=message.caption, progress=make_progress("Yuborilmoqda")),
                                timeout=send_timeout
                            )
                        else:
                            await asyncio.wait_for(
                                client.send_document(target_chat_id, document=file_path, caption=message.caption, progress=make_progress("Yuborilmoqda")),
                                timeout=send_timeout
                            )
                        success_count += 1
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout on msg_id {message.id}, skipping...")
                    transfer_states[user_id]["message"] = f"{msg_num}/{total_count}: Vaqt tugadi (timeout), o'tkazildi"
                except FloodWait as fw:
                    logger.warning(f"FloodWait on msg_id {message.id}: {fw.value}s")
                    transfer_states[user_id]["message"] = f"Telegram cheklovi: {fw.value}s kutilmoqda..."
                    await asyncio.sleep(min(fw.value, 30))
                except Exception as inner_e:
                    logger.error(f"Faylni yuklash/yuborishda xatolik msg_id {message.id}: {inner_e}")
                finally:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass

            count += 1
            transfer_states[user_id]["current"] = count
            await asyncio.sleep(1.0)
            
        transfer_states[user_id]["status"] = "completed"
        transfer_states[user_id]["message"] = f"Muvaffaqiyatli yakunlandi! {success_count} ta media ko'chirildi."
        logger.info(f"Ko'chirish yakunlandi (User {user_id}): {success_count}/{count}")

    except FloodWait as e:
        transfer_states[user_id]["status"] = "error"
        transfer_states[user_id]["message"] = f"Telegram cheklovi: {e.value} soniya kuting."
    except Exception as e:
        logger.error(f"Transfer umumiy xato: {e}", exc_info=True)
        transfer_states[user_id]["status"] = "error"
        transfer_states[user_id]["message"] = f"Xatolik: {e}"
