"""
============================================================
 generate_session.py — Pyrogram Session String Generatori
============================================================
 Foydalanish:
   1. .env faylida API_ID va API_HASH ni to'ldiring
   2. python generate_session.py
   3. Chiqgan session stringni .env faylidagi
      PYROGRAM_SESSION_STRING ga nusxalab qo'ying
============================================================
"""

import os
import asyncio
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
from dotenv import load_dotenv

load_dotenv()

API_ID   = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

async def generate_session():
    if not API_ID or not API_HASH:
        print("❌ Xato: .env faylida API_ID va API_HASH ni to'ldiring!")
        return

    print("=" * 60)
    print("  Pyrogram Session String Generator (Yangi usul)")
    print("=" * 60)
    
    # Telefon raqamni Python orqali so'raymiz (bu terminalda qotmaydi)
    phone = input("📱 Telefon raqamingizni kiriting (Misol: +998901234567): ")
    
    print("⏳ Telegram serveriga ulanilmoqda...")
    client = Client("my_account", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    
    await client.connect()
    
    try:
        # Kod yuborish
        sent_code = await client.send_code(phone)
        print("✅ Telegramga tasdiqlash kodi yuborildi!")
        
        # Kodni so'rash
        code = input("✉️ Kelgan kodni kiriting: ")
        
        try:
            await client.sign_in(phone, sent_code.phone_code_hash, code)
        except SessionPasswordNeeded:
            # 2FA parol so'rash
            pwd = input("🔐 2-bosqichli (2FA) parolingizni kiriting: ")
            await client.check_password(pwd)
            
        session_string = await client.export_session_string()
        print("\n" + "=" * 60)
        print("✅ Session string muvaffaqiyatli yaratildi!")
        print("=" * 60)
        print("\n📋 Quyidagi stringni .env faylidagi yoki Railway'dagi")
        print("   PYROGRAM_SESSION_STRING= ga nusxalab qo'ying:\n")
        print(session_string)
        print("\n" + "=" * 60)
        
    except Exception as e:
        print(f"❌ Xatolik yuz berdi: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(generate_session())
