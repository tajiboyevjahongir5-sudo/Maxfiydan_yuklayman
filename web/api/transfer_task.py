import asyncio
import logging
from pyrogram import Client
from pyrogram.enums import MessageMediaType
from pyrogram.errors import FloodWait, ChatForwardsRestricted
import os
from config import config

logger = logging.getLogger(__name__)

async def run_transfer(client: Client, source_chat_id: int, target_chat_id: int, media_type: str):
    logger.info(f"Ko'chirish boshlandi: {source_chat_id} -> {target_chat_id} ({media_type})")
    
    count = 0
    try:
        # iterate from oldest to newest by getting history and reversing, or just get_chat_history
        # By default get_chat_history gets newest first. Let's process newest first for simplicity, or reverse it.
        messages = []
        async for message in client.get_chat_history(source_chat_id):
            if not message.media:
                continue
                
            if media_type == "photo" and message.media != MessageMediaType.PHOTO:
                continue
            if media_type == "video" and message.media != MessageMediaType.VIDEO:
                continue
            
            messages.append(message)
            if len(messages) > 100: # process in batches to save memory
                break
                
        # reverse to process oldest first (from the batch)
        messages.reverse()
        
        for message in messages:
            try:
                # Try simple copy first
                await client.copy_message(
                    chat_id=target_chat_id,
                    from_chat_id=source_chat_id,
                    message_id=message.id,
                    caption=message.caption
                )
            except Exception as e:
                if "RESTRICTED" in str(e).upper() or "INVALID" in str(e).upper():
                    # If restricted, download and upload
                    logger.info(f"Restricted channel, downloading msg {message.id}...")
                    file_path = await client.download_media(message, file_name=str(config.download_dir) + "/")
                    if file_path:
                        if message.media == MessageMediaType.PHOTO:
                            await client.send_photo(target_chat_id, photo=file_path, caption=message.caption)
                        elif message.media == MessageMediaType.VIDEO:
                            await client.send_video(target_chat_id, video=file_path, caption=message.caption)
                        else:
                            await client.send_document(target_chat_id, document=file_path, caption=message.caption)
                        os.remove(file_path)
                else:
                    logger.error(f"Copy failed: {e}")
                    raise e
                    
            count += 1
            await asyncio.sleep(2) # rate limit protection
            
        logger.info(f"Ko'chirish tugadi. {count} ta media ko'chirildi.")
    except Exception as e:
        logger.error(f"Transfer error: {e}")
