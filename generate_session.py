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

import asyncio
import os
from pyrogram import Client
from dotenv import load_dotenv

load_dotenv()

API_ID   = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")


async def generate_session() -> None:
    """Interaktiv tarzda telefon raqam orqali login qilib session string hosil qiladi."""
    if not API_ID or not API_HASH:
        print("❌ Xato: .env faylida API_ID va API_HASH ni to'ldiring!")
        return

    print("=" * 60)
    print("  Pyrogram Session String Generator")
    print("=" * 60)
    print("⚠️  Eslatma: Telefon raqamingizni xalqaro formatda kiriting.")
    print("    Misol: +998901234567\n")

    async with Client(
        name=":memory:",  # Faylga saqlamasdan xotiradan foydalanish
        api_id=API_ID,
        api_hash=API_HASH,
    ) as app:
        session_string = await app.export_session_string()
        print("\n" + "=" * 60)
        print("✅ Session string muvaffaqiyatli yaratildi!")
        print("=" * 60)
        print("\n📋 Quyidagi stringni .env faylidagi")
        print("   PYROGRAM_SESSION_STRING= ga nusxalab qo'ying:\n")
        print(session_string)
        print("\n" + "=" * 60)
        print("⚠️  MUHIM: Ushbu session string maxfiy! Hech kimga bermang.")
        print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(generate_session())
