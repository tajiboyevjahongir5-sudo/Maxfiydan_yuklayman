import random
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from database import async_session, Tariff, UserTariff, Payment, BotSettings
from sqlalchemy import select
from datetime import datetime, timedelta
from web.auth import get_current_user_id

router = APIRouter(prefix="/api/billing", tags=["billing"])

PAYMENT_TIMEOUT_SECONDS = 120  # 2 daqiqa


# ─── Tariff ro'yxati ────────────────────────────────────────────────────────
@router.get("/tariffs")
async def get_tariffs():
    """Barcha mavjud tariflarni qaytaradi."""
    async with async_session() as db:
        result = await db.execute(select(Tariff))
        tariffs = result.scalars().all()
        return [
            {
                "id": t.id,
                "name": t.name,
                "price": t.price,
                "max_gb": round(t.max_file_size_bytes / (1024**3), 1),
                "max_downloads_per_day": t.max_downloads_per_day,
                "duration_days": t.duration_days
            } for t in tariffs
        ]


# ─── Checkout: unikal summa yaratish ────────────────────────────────────────
class CheckoutRequest(BaseModel):
    tariff_id: int

class CheckoutResponse(BaseModel):
    payment_id: int
    unique_amount: int      # Foydalanuvchi o'tkazadigan aniq summa
    card_number: str
    card_holder: str
    bank_name: str
    tariff_name: str
    expires_seconds: int = PAYMENT_TIMEOUT_SECONDS

@router.post("/checkout", response_model=CheckoutResponse)
async def initiate_payment(req: CheckoutRequest, user_id: int = Depends(get_current_user_id)):
    """
    To'lov jarayonini boshlaydi.
    - Narxga 1-100 orasidagi tasodifiy raqam qo'shib unikal summa yaratadi.
    - Shu raqam hozir boshqa foydalanuvchi tomonidan ishlatilmayotganini tekshiradi.
    - 2 daqiqadan keyin to'lov avtomatik 'expired' holatiga o'tadi va 
      shu tasodifiy raqam boshqalarga berilishi mumkin.
    """
    async with async_session() as db:
        # Tarifni olish
        tariff = await db.get(Tariff, req.tariff_id)
        if not tariff:
            raise HTTPException(status_code=404, detail="Tarif topilmadi")

        # Bot sozlamalarini olish
        settings_result = await db.execute(select(BotSettings).where(BotSettings.id == 1))
        settings = settings_result.scalar_one_or_none()
        if not settings or not settings.card_number:
            raise HTTPException(
                status_code=503,
                detail="To'lov tizimi sozlanmagan. Admin bilan bog'laning."
            )

        # Foydalanuvchining eski pending to'lovini bekor qilish
        old_result = await db.execute(
            select(Payment).where(
                Payment.user_id == user_id,
                Payment.status == "pending"
            )
        )
        for old in old_result.scalars().all():
            old.status = "cancelled"

        # Hozir faol (pending, muddati o'tmagan) to'lovlarda qaysi 
        # tasodifiy raqamlar (1-100) ishlatilayotganini aniqlash
        now = datetime.utcnow()
        active_result = await db.execute(
            select(Payment).where(
                Payment.status == "pending",
                Payment.expires_at > now
            )
        )
        active_payments = active_result.scalars().all()

        # Faol to'lovlardagi qo'shilgan raqamlarni hisoblash
        # unique_amount = price + random_addition, shuning uchun:
        # random_addition = unique_amount - int(tariff.price)
        # Lekin har xil tariflar uchun narx farq qiladi, shuning uchun
        # barcha faol unique_amount larning oxirgi 1-100 qatorini saqlaymiz
        used_amounts = set(p.unique_amount for p in active_payments)

        # 1-100 orasidagi bo'sh raqam topish
        available = list(range(1, 101))
        random.shuffle(available)

        chosen_addition = None
        for addition in available:
            candidate = int(tariff.price) + addition
            if candidate not in used_amounts:
                chosen_addition = addition
                break

        if chosen_addition is None:
            # Barcha 100 ta slot band (juda kam hollarda)
            raise HTTPException(
                status_code=503,
                detail="Tizim band. Iltimos, 2 daqiqadan keyin qayta urinib ko'ring."
            )

        unique_amount = int(tariff.price) + chosen_addition
        expires_at = now + timedelta(seconds=PAYMENT_TIMEOUT_SECONDS)

        # Yangi to'lov yozuvi
        new_payment = Payment(
            user_id=user_id,
            tariff_id=req.tariff_id,
            amount=tariff.price,
            unique_amount=unique_amount,
            provider="card",
            status="pending",
            expires_at=expires_at
        )
        db.add(new_payment)
        await db.commit()
        await db.refresh(new_payment)

        return CheckoutResponse(
            payment_id=new_payment.id,
            unique_amount=unique_amount,
            card_number=settings.card_number,
            card_holder=settings.card_holder or "Karta egasi",
            bank_name=settings.bank_name or "Bank",
            tariff_name=tariff.name
        )


# ─── To'lov holatini tekshirish (polling) ───────────────────────────────────
@router.get("/payment/{payment_id}/status")
async def check_payment_status(payment_id: int, user_id: int = Depends(get_current_user_id)):
    """
    Frontend har 5 soniyada shu endpointni chaqirib to'lov holatini tekshiradi.
    Agar expires_at o'tib ketgan bo'lsa, status 'expired' ga o'tkaziladi.
    """
    async with async_session() as db:
        payment = await db.get(Payment, payment_id)
        if not payment or payment.user_id != user_id:
            raise HTTPException(status_code=404, detail="To'lov topilmadi")

        # Muddati o'tgan bo'lsa avtomatik expire qilish
        if payment.status == "pending" and datetime.utcnow() > payment.expires_at:
            payment.status = "expired"
            await db.commit()

        seconds_left = max(0, int((payment.expires_at - datetime.utcnow()).total_seconds()))

        return {
            "status": payment.status,
            "payment_id": payment.id,
            "seconds_left": seconds_left
        }
