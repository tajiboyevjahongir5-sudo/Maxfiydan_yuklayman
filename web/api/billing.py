from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from database import async_session, Tariff, UserTariff, Payment, User
from sqlalchemy import select
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/billing", tags=["billing"])

class CheckoutRequest(BaseModel):
    tariff_id: int
    provider: str # 'click' or 'payme'

@router.get("/tariffs")
async def get_tariffs():
    """Barcha mavjud tariflarni qaytaradi."""
    async with async_session() as db:
        result = await db.execute(select(Tariff))
        tariffs = result.scalars().all()
        return tariffs

@router.post("/checkout")
async def process_mock_payment(req: CheckoutRequest, user_id: int):
    """
    Mock to'lov tizimi.
    Haqiqiy loyihada bu yerda Click/Payme bilan integratsiya qilinadi.
    Hozir esa to'lov darhol o'tdi deb hisoblaymiz.
    """
    async with async_session() as db:
        # Tarifni olish
        tariff = await db.get(Tariff, req.tariff_id)
        if not tariff:
            raise HTTPException(status_code=404, detail="Tarif topilmadi")
            
        # To'lov yozuvini yaratish
        payment = Payment(
            user_id=user_id,
            amount=tariff.price,
            provider=req.provider,
            status="completed"
        )
        db.add(payment)
        
        # User balansini (yoki ro'yxatini) yangilash - to'g'ridan to'g'ri tarif beramiz
        result = await db.execute(select(UserTariff).where(UserTariff.user_id == user_id))
        user_tariff = result.scalar_one_or_none()
        
        new_expiry = datetime.utcnow() + timedelta(days=tariff.duration_days)
        
        if user_tariff:
            user_tariff.tariff_id = tariff.id
            user_tariff.expires_at = new_expiry
        else:
            new_user_tariff = UserTariff(
                user_id=user_id,
                tariff_id=tariff.id,
                expires_at=new_expiry
            )
            db.add(new_user_tariff)
            
        await db.commit()
        
        return {
            "status": "success", 
            "message": f"To'lov muvaffaqiyatli amalga oshirildi! Siz endi '{tariff.name}' tarifidasiz."
        }
