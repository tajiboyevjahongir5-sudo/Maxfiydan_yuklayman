"""
============================================================
 payment_handler.py — To'lov kanalini kuzatish handleri
============================================================
 Bot to'lov kanalini kuzatib, agar xabarda pending to'lovlar
 ro'yxatidagi unikal summa topilsa — tarif avtomatik faollashtiriladi.
============================================================
"""

import re
import logging
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import Message

from database import async_session, Payment, UserTariff, BotSettings
from sqlalchemy import select

logger = logging.getLogger(__name__)

payment_router = Router(name="payment_router")


def extract_amounts_from_text(text: str) -> list[int]:
    """Xabar matnidan barcha raqamlarni ajratib oladi."""
    # 15,000 yoki 15.000 yoki 15000 formatlarni topadi
    raw = re.sub(r"[,.\s]", "", text)
    numbers = re.findall(r"\d{4,7}", raw)  # 4-7 xonali raqamlar (summa doirasida)
    return [int(n) for n in numbers]


@payment_router.message()
async def handle_payment_channel_message(message: Message) -> None:
    """
    Har qanday xabarni ushlab, to'lov kanalidan kelgan bo'lsa tekshiradi.
    To'lov kanalida foydalanuvchi kvitansiya skrinshotini yuborganda,
    xabardagi matndan unikal summani topadi va to'lovni tasdiqlaydi.
    """
    # Kanal ID ni bazadan olish
    async with async_session() as db:
        settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_result.scalar_one_or_none()

    if not settings or not settings.payment_channel_id:
        return  # Kanal sozlanmagan, chiqib ketish

    # Faqat to'lov kanalidan kelgan xabarlarni qabul qilish
    chat_id = message.chat.id
    # Kanallar manfiy ID bilan keladi, foydalanuvchi ID ni tekshirish
    if chat_id != settings.payment_channel_id:
        return

    # Xabardagi barcha raqamlarni ajratib olish
    text = message.text or message.caption or ""
    if not text:
        return

    found_amounts = extract_amounts_from_text(text)
    if not found_amounts:
        return

    logger.info(f"💳 To'lov kanalida xabar topildi. Raqamlar: {found_amounts}")

    # Pending to'lovlar orasida mos unikal summani izlash
    async with async_session() as db:
        now = datetime.utcnow()
        for amount in found_amounts:
            result = await db.execute(
                select(Payment).where(
                    Payment.unique_amount == amount,
                    Payment.status == "pending",
                    Payment.expires_at > now   # Muddati o'tmagan bo'lishi shart
                )
            )
            payment = result.scalar_one_or_none()

            if payment:
                logger.info(f"✅ To'lov tasdiqlandi! Payment ID: {payment.id}, Summa: {amount}")

                # 1. To'lovni tasdiqlash
                payment.status = "completed"

                # 2. Tarif faollashtirish
                tariff_result = await db.get(
                    __import__("database", fromlist=["Tariff"]).Tariff, payment.tariff_id
                )
                if tariff_result:
                    new_expiry = datetime.utcnow() + timedelta(days=tariff_result.duration_days)

                    user_tariff_result = await db.execute(
                        select(UserTariff).where(UserTariff.user_id == payment.user_id)
                    )
                    existing_tariff = user_tariff_result.scalar_one_or_none()

                    if existing_tariff:
                        existing_tariff.tariff_id = payment.tariff_id
                        existing_tariff.expires_at = new_expiry
                    else:
                        new_user_tariff = UserTariff(
                            user_id=payment.user_id,
                            tariff_id=payment.tariff_id,
                            expires_at=new_expiry
                        )
                        db.add(new_user_tariff)

                await db.commit()

                # 3. Foydalanuvchiga xabar yuborish
                try:
                    from bot_instance import bot
                    await bot.send_message(
                        chat_id=payment.user_id,
                        text=(
                            f"✅ <b>To'lovingiz tasdiqlandi!</b>\n\n"
                            f"🎉 <b>{tariff_result.name if tariff_result else 'Tarif'}</b> tarifi "
                            f"muvaffaqiyatli faollashtirildi!\n\n"
                            f"📱 Botdan to'liq foydalanishingiz mumkin."
                        ),
                        parse_mode="HTML"
                    )
                    logger.info(f"📨 Foydalanuvchi {payment.user_id} ga to'lov tasdiqlanishi haqida xabar yuborildi")
                except Exception as e:
                    logger.error(f"Foydalanuvchiga xabar yuborishda xato: {e}")

                break  # Bitta to'lov tasdiqlandi, keyingilariga o'tish shart emas
