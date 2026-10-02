import asyncio
import inspect
import logging
import math
import os
import shutil
import uuid
import aiofiles
from typing import Union, Optional
from pyrogram import Client
from pyrogram.enums import MessageMediaType, MessagesFilter
from pyrogram.errors import FloodWait
from config import config
from utils import parse_target_chat, extract_mp4_metadata

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

def get_media_info(msg):
    """Xabar ichidagi mediani, uning noyob ID sini, hajmini va turini aniqlaydi."""
    if msg.photo:
        return "photo", msg.photo.file_unique_id, msg.photo.file_size or 0, ".jpg", "📷 Rasm"
    if msg.video_note:
        return "video_note", msg.video_note.file_unique_id, msg.video_note.file_size or 0, ".mp4", "📹 Dumaloq video"
    if msg.video:
        return "video", msg.video.file_unique_id, msg.video.file_size or 0, ".mp4", "🎥 Video"
    if msg.animation:
        return "animation", msg.animation.file_unique_id, msg.animation.file_size or 0, ".mp4", "🎞 Animatsiya"
    if msg.document:
        fn = msg.document.file_name or ""
        ext = os.path.splitext(fn)[1] or ".bin"
        return "document", msg.document.file_unique_id, msg.document.file_size or 0, ext, "📄 Hujjat"
    if msg.audio:
        return "audio", msg.audio.file_unique_id, msg.audio.file_size or 0, ".mp3", "🎵 Audio"
    return None, None, 0, ".bin", "Fayl"

async def fast_download_media(
    client: Client,
    message,
    dest_file: str,
    file_size: int,
    progress_callback = None
) -> str:
    """
    Katta fayllarni Telegram DC serverlaridan 4 ta parallel MTProto oqimida (multi-chunk)
    juda tez yuklab oladi. 5MB dan kichik fayllar yoki nosozlikda standart yuklab olishga o'tadi.
    """
    if file_size <= 5 * 1024 * 1024 or not hasattr(client, "stream_media"):
        return await client.download_media(message, file_name=dest_file, progress=progress_callback)

    chunk_size = 1024 * 1024  # 1 MiB
    total_chunks = math.ceil(file_size / chunk_size)
    num_workers = min(4, total_chunks)

    if num_workers <= 1:
        return await client.download_media(message, file_name=dest_file, progress=progress_callback)

    chunks_per_worker = math.ceil(total_chunks / num_workers)
    downloaded_bytes = 0
    lock = asyncio.Lock()

    async def _worker(w_idx: int, offset: int, limit: int):
        nonlocal downloaded_bytes
        part_file = f"{dest_file}.part{w_idx}"
        async with aiofiles.open(part_file, "wb") as f:
            async for chunk in client.stream_media(message, limit=limit, offset=offset):
                await f.write(chunk)
                async with lock:
                    downloaded_bytes += len(chunk)
                    cur_bytes = downloaded_bytes
                if progress_callback:
                    try:
                        res = progress_callback(min(cur_bytes, file_size), file_size)
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass

    tasks = []
    for i in range(num_workers):
        start_chunk = i * chunks_per_worker
        end_chunk = min((i + 1) * chunks_per_worker, total_chunks)
        limit = end_chunk - start_chunk
        if limit > 0:
            tasks.append(_worker(i, start_chunk, limit))

    try:
        await asyncio.gather(*tasks)

        # Barcha qismlarni bitta faylga birlashtirish
        with open(dest_file, "wb") as out_f:
            for i in range(num_workers):
                part_file = f"{dest_file}.part{i}"
                if os.path.exists(part_file):
                    with open(part_file, "rb") as in_f:
                        shutil.copyfileobj(in_f, out_f, length=1024 * 1024)
                    try:
                        os.remove(part_file)
                    except Exception:
                        pass

        if os.path.exists(dest_file) and os.path.getsize(dest_file) > 0:
            return dest_file
        else:
            raise ValueError("Fayl qismlari birlashtirilmadi yoki bo'sh")

    except Exception as exc:
        logger.warning(f"Parallel yuklashda ogohlantirish ({exc}), standart yuklab olishga o'tilmoqda...")
        for i in range(num_workers):
            part_file = f"{dest_file}.part{i}"
            if os.path.exists(part_file):
                try:
                    os.remove(part_file)
                except Exception:
                    pass
        if os.path.exists(dest_file):
            try:
                os.remove(dest_file)
            except Exception:
                pass
        return await client.download_media(message, file_name=dest_file, progress=progress_callback)

async def run_transfer(user_id: int, client: Client, source_chat_id: Union[int, str], target_chat_id: Union[int, str], media_type: str):
    source_chat_id = parse_target_chat(source_chat_id)
    target_chat_id = parse_target_chat(target_chat_id)
    logger.info(f"Ko'chirish boshlandi (User {user_id}): {source_chat_id} -> {target_chat_id} ({media_type})")
    
    cancel_flags[user_id] = False
    transfer_states[user_id] = {"total": 0, "current": 0, "status": "initializing", "message": "Kanal/guruh ma'lumotlari tekshirilmoqda..."}
    
    try:
        config.download_dir.mkdir(parents=True, exist_ok=True)
        
        # Peer xatosi (PEER_ID_INVALID) ning oldini olish uchun bazani qizdirish
        source_chat = None
        target_chat = None
        try:
            source_chat = await asyncio.wait_for(client.get_chat(source_chat_id), timeout=8.0)
        except Exception as e:
            logger.info(f"Source peer get_chat xatosi ({e}), dialoglar tekshirilmoqda...")
            try:
                async for dialog in client.get_dialogs(limit=300):
                    if cancel_flags.get(user_id):
                        return
                    if dialog.chat and (dialog.chat.id == source_chat_id or getattr(dialog.chat, "username", None) == source_chat_id):
                        source_chat = dialog.chat
                        break
            except Exception:
                pass

        try:
            target_chat = await asyncio.wait_for(client.get_chat(target_chat_id), timeout=8.0)
        except Exception as e:
            logger.info(f"Target peer get_chat xatosi ({e}), dialoglar tekshirilmoqda...")
            try:
                async for dialog in client.get_dialogs(limit=300):
                    if cancel_flags.get(user_id):
                        return
                    if dialog.chat and (dialog.chat.id == target_chat_id or getattr(dialog.chat, "username", None) == target_chat_id):
                        target_chat = dialog.chat
                        break
            except Exception:
                pass

        if source_chat:
            source_chat_id = source_chat.id
        if target_chat:
            target_chat_id = target_chat.id

        if cancel_flags.get(user_id):
            return

        # Qidiruv filtrlari (Video yoki All tanlansa, dumaloq video - VIDEO_NOTE ham qidiriladi)
        filters = []
        if media_type == "photo":
            filters = [MessagesFilter.PHOTO]
        elif media_type == "video":
            # Oddiy video + Dumaloq video (VIDEO_NOTE) + Animatsiya (ANIMATION)
            filters = [MessagesFilter.VIDEO, MessagesFilter.VIDEO_NOTE, MessagesFilter.ANIMATION]
        else: # "all"
            # Rasm, video + Dumaloq video + Animatsiya
            filters = [MessagesFilter.PHOTO_VIDEO, MessagesFilter.VIDEO_NOTE, MessagesFilter.ANIMATION]
            
        transfer_states[user_id]["message"] = "Medialar aniqlanmoqda..."
        
        # Barcha xabarlarni yig'ish (id bo'yicha takrorlanishsiz)
        messages_dict = {}
        for f in filters:
            if cancel_flags.get(user_id):
                return
            try:
                async for message in client.search_messages(chat_id=source_chat_id, filter=f):
                    if cancel_flags.get(user_id):
                        return
                    if message and message.id not in messages_dict:
                        messages_dict[message.id] = message
                    if len(messages_dict) % 30 == 0:
                        transfer_states[user_id]["message"] = f"Xabarlar aniqlanmoqda ({len(messages_dict)} ta)..."
            except Exception as e:
                logger.warning(f"search_messages xatosi ({f}): {e}")

        # Agar search_messages da xabar topilmasa (masalan qidiruv cheklangan maxfiy guruhlarda),
        # get_chat_history orqali to'g'ridan-to'g'ri xabarlar tarixidan o'qiladi
        if not messages_dict:
            logger.info("search_messages da hech narsa topilmadi, get_chat_history orqali to'g'ridan-to'g'ri o'qilmoqda...")
            transfer_states[user_id]["message"] = "Guruh xabarlari to'g'ridan-to'g'ri tekshirilmoqda..."
            try:
                async for message in client.get_chat_history(chat_id=source_chat_id, limit=300):
                    if cancel_flags.get(user_id):
                        return
                    if not message:
                        continue
                    m_type, file_uid, _, _, _ = get_media_info(message)
                    if not m_type:
                        continue
                    if media_type == "photo" and m_type == "photo":
                        messages_dict[message.id] = message
                    elif media_type == "video" and m_type in ["video", "video_note", "animation"]:
                        messages_dict[message.id] = message
                    elif media_type == "all":
                        messages_dict[message.id] = message
                    if len(messages_dict) % 30 == 0:
                        transfer_states[user_id]["message"] = f"Guruhdan medialar aniqlanmoqda ({len(messages_dict)} ta)..."
            except Exception as hist_err:
                logger.warning(f"get_chat_history xatosi: {hist_err}")

        if not messages_dict:
            transfer_states[user_id]["status"] = "completed"
            transfer_states[user_id]["message"] = "Ko'chirish uchun hech qanday media topilmadi!"
            return

        # Xabarlarni vaqt tartibida (kichik id dan kattasiga) saralaymiz
        messages_to_copy = sorted(messages_dict.values(), key=lambda m: m.id)
        total_count = len(messages_to_copy)
        transfer_states[user_id]["total"] = total_count
        transfer_states[user_id]["current"] = 0

        # Takroriy yuborishning oldini olish uchun yuborilgan fayl ID lari to'plami
        sent_unique_ids = set()
        count = 0
        success_count = 0

        # Manba kanalida himoya (noforwards) bor-yo'qligini tekshirish
        is_protected = getattr(source_chat, "has_protected_content", False) if source_chat else False
        can_batch_copy = not is_protected

        # ─── 🚀 3-USUL: TEZKOR PAKETLAB KO'CHIRISH (Ochiq kanallar uchun) ─────
        if can_batch_copy:
            BATCH_SIZE = 25
            logger.info(f"🚀 Fast Batch Copy rejimida boshlandi (User {user_id}, {total_count} ta xabar)")
            
            for i in range(0, total_count, BATCH_SIZE):
                if cancel_flags.get(user_id):
                    transfer_states[user_id]["status"] = "error"
                    transfer_states[user_id]["message"] = "Ko'chirish to'xtatildi."
                    return

                batch = messages_to_copy[i:i + BATCH_SIZE]
                
                # Takroriy bir xil fayllarni filtrlash
                filtered_batch = []
                for m in batch:
                    _, uid, _, _, _ = get_media_info(m)
                    if uid and uid in sent_unique_ids:
                        continue
                    if uid:
                        sent_unique_ids.add(uid)
                    filtered_batch.append(m)

                if not filtered_batch:
                    count += len(batch)
                    transfer_states[user_id]["current"] = count
                    continue

                batch_ids = [m.id for m in filtered_batch]
                try:
                    await asyncio.wait_for(
                        client.forward_messages(
                            chat_id=target_chat_id,
                            from_chat_id=source_chat_id,
                            message_ids=batch_ids,
                            drop_author=True
                        ),
                        timeout=15.0
                    )
                    success_count += len(filtered_batch)
                    count += len(batch)
                    transfer_states[user_id]["current"] = count
                    transfer_states[user_id]["message"] = f"Tezkor ko'chirilmoqda ({count}/{total_count})..."
                    await asyncio.sleep(0.3)
                except Exception as batch_err:
                    err_str = str(batch_err).upper()
                    if "RESTRICTED" in err_str:
                        # Himoyalangan ekan — fallback rejimiga o'tamiz
                        can_batch_copy = False
                        messages_to_copy = messages_to_copy[i:]
                        break
                    else:
                        count += len(batch)
                        transfer_states[user_id]["current"] = count

            if can_batch_copy:
                transfer_states[user_id]["status"] = "completed"
                transfer_states[user_id]["message"] = f"Muvaffaqiyatli yakunlandi! {success_count} ta media ko'chirildi."
                logger.info(f"Fast Batch Transfer yakunlandi (User {user_id}): {success_count}/{count}")
                return

        # ─── 🛡️ ZAXIRA REJIMI: HIMOYALANGAN (NOFORWARDS) KANALLAR UCHUN ───────
        logger.info(f"Yopiq/himoyalangan kanal: yuklab olib yuborish rejimida davom etilmoqda (User {user_id})")
        
        for message in messages_to_copy:
            if cancel_flags.get(user_id):
                transfer_states[user_id]["status"] = "error"
                transfer_states[user_id]["message"] = "Ko'chirish to'xtatildi."
                return

            msg_num = count + 1
            m_type, file_uid, file_size, ext, label = get_media_info(message)

            # Takroriy bir xil rasm yoki video 2 marta yuborilmasin
            if file_uid and file_uid in sent_unique_ids:
                logger.info(f"Takroriy fayl o'tkazib yuborildi ({file_uid}) msg_id: {message.id}")
                count += 1
                transfer_states[user_id]["current"] = count
                continue

            if file_uid:
                sent_unique_ids.add(file_uid)

            transfer_states[user_id]["message"] = f"{msg_num}/{total_count}: {label} ko'chirilmoqda..."

            # 300 MB dan katta fayllarni xavfsizlik uchun o'tkazib yuboramiz
            if file_size > 300 * 1024 * 1024:
                logger.warning(f"Fayl juda katta ({file_size / (1024*1024):.1f} MB), o'tkazildi.")
                transfer_states[user_id]["message"] = f"{msg_num}/{total_count}: Juda katta (>300MB), o'tkazildi"
                count += 1
                transfer_states[user_id]["current"] = count
                continue

            file_path = None
            try:
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
                                transfer_states[user_id]["message"] = f"{msg_num}/{total_count} {label} {action_name}: {cur_mb:.1f}/{tot_mb:.1f} MB ({pct:.0f}%)"
                    return _prog

                unique_name = f"transfer_{user_id}_{uuid.uuid4().hex[:6]}{ext}"
                dest_file = str(config.download_dir / unique_name)
                download_timeout = max(120.0, file_size / (300 * 1024)) if file_size else 120.0
                send_timeout = max(120.0, file_size / (300 * 1024)) if file_size else 120.0

                # 1. Tezkor ko'p oqimli (Multi-chunk) yuklab olish
                file_path = await asyncio.wait_for(
                    fast_download_media(
                        client,
                        message, 
                        dest_file=dest_file,
                        file_size=file_size,
                        progress_callback=make_progress("yuklab olinmoqda")
                    ),
                    timeout=download_timeout
                )

                if file_path and os.path.exists(file_path):

                    if m_type == "photo":
                        try:
                            await asyncio.wait_for(
                                client.send_photo(
                                    target_chat_id, 
                                    photo=file_path, 
                                    caption=message.caption, 
                                    progress=make_progress("yuborilmoqda")
                                ),
                                timeout=send_timeout
                            )
                            success_count += 1
                        except Exception as photo_err:
                            err_s = str(photo_err).upper()
                            # Faqat format/o'lcham xatosi bo'lsa hujjat sifatida yuboriladi (ikkita bir xil bo'lmasligi uchun)
                            if any(k in err_s for k in ["PHOTO_INVALID", "IMAGE_PROCESS", "MEDIA_EMPTY"]):
                                await asyncio.wait_for(
                                    client.send_document(
                                        target_chat_id, 
                                        document=file_path, 
                                        caption=message.caption, 
                                        progress=make_progress("hujjat sifatida yuborilmoqda")
                                    ),
                                    timeout=send_timeout
                                )
                                success_count += 1

                    elif m_type == "video_note":
                        # 📹 DUMALOQ VIDEO (VIDEO NOTE)
                        try:
                            duration = message.video_note.duration if message.video_note else 0
                            length = message.video_note.length if message.video_note else 1
                            await asyncio.wait_for(
                                client.send_video_note(
                                    target_chat_id, 
                                    video_note=file_path, 
                                    duration=duration,
                                    length=length,
                                    progress=make_progress("dumaloq video yuborilmoqda")
                                ),
                                timeout=send_timeout
                            )
                            success_count += 1
                        except Exception as vn_err:
                            logger.warning(f"send_video_note xatosi ({vn_err}), oddiy video sifatida yuborilmoqda...")
                            v_w, v_h, v_dur = extract_mp4_metadata(file_path)
                            await asyncio.wait_for(
                                client.send_video(
                                    target_chat_id, 
                                    video=file_path, 
                                    caption=message.caption,
                                    width=v_w or 0,
                                    height=v_h or 0,
                                    duration=v_dur or duration,
                                    progress=make_progress("video sifatida yuborilmoqda")
                                ),
                                timeout=send_timeout
                            )
                            success_count += 1

                    elif m_type == "video":
                        v_width = getattr(message.video, "width", 0) or 0
                        v_height = getattr(message.video, "height", 0) or 0
                        v_duration = getattr(message.video, "duration", 0) or 0
                        v_supports_streaming = getattr(message.video, "supports_streaming", True)
                        v_file_name = getattr(message.video, "file_name", None)

                        # Agar kenglik yoki balandlik 0 bo'lsa, MP4 fayl sarlavhasidan aniqlaymiz
                        if v_width == 0 or v_height == 0:
                            f_w, f_h, f_dur = extract_mp4_metadata(file_path)
                            if f_w > 0 and f_h > 0:
                                v_width, v_height = f_w, f_h
                            if v_duration == 0 and f_dur > 0:
                                v_duration = f_dur

                        await asyncio.wait_for(
                            client.send_video(
                                target_chat_id, 
                                video=file_path, 
                                caption=message.caption,
                                width=v_width,
                                height=v_height,
                                duration=v_duration,
                                supports_streaming=v_supports_streaming,
                                file_name=v_file_name,
                                progress=make_progress("video yuborilmoqda")
                            ),
                            timeout=send_timeout
                        )
                        success_count += 1

                    elif m_type == "animation":
                        a_width = getattr(message.animation, "width", 0) or 0
                        a_height = getattr(message.animation, "height", 0) or 0
                        a_duration = getattr(message.animation, "duration", 0) or 0
                        if a_width == 0 or a_height == 0:
                            f_w, f_h, f_dur = extract_mp4_metadata(file_path)
                            if f_w > 0 and f_h > 0:
                                a_width, a_height = f_w, f_h
                            if a_duration == 0 and f_dur > 0:
                                a_duration = f_dur

                        await asyncio.wait_for(
                            client.send_animation(
                                target_chat_id,
                                animation=file_path,
                                caption=message.caption,
                                width=a_width,
                                height=a_height,
                                duration=a_duration,
                                progress=make_progress("animatsiya yuborilmoqda")
                            ),
                            timeout=send_timeout
                        )
                        success_count += 1

                    elif m_type == "audio":
                        a_duration = getattr(message.audio, "duration", 0) or 0
                        a_performer = getattr(message.audio, "performer", None)
                        a_title = getattr(message.audio, "title", None)
                        await asyncio.wait_for(
                            client.send_audio(
                                target_chat_id,
                                audio=file_path,
                                caption=message.caption,
                                duration=a_duration,
                                performer=a_performer,
                                title=a_title,
                                progress=make_progress("audio yuborilmoqda")
                            ),
                            timeout=send_timeout
                        )
                        success_count += 1

                    else:
                        d_file_name = getattr(message.document, "file_name", None) if message.document else None
                        await asyncio.wait_for(
                            client.send_document(
                                target_chat_id, 
                                document=file_path, 
                                caption=message.caption,
                                file_name=d_file_name,
                                progress=make_progress("hujjat yuborilmoqda")
                            ),
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
                for i in range(4):
                    pf = f"{dest_file}.part{i}"
                    if os.path.exists(pf):
                        try:
                            os.remove(pf)
                        except Exception:
                            pass

            count += 1
            transfer_states[user_id]["current"] = count
            await asyncio.sleep(0.3)
            
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
