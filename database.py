import asyncio
from datetime import datetime
from typing import Optional, List
from sqlalchemy import BigInteger, String, Boolean, Integer, DateTime, ForeignKey, Float, Text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from config import config

# ─── Database Engine Setup ──────────────────────────────────────────────────
engine = create_async_engine(config.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

# ─── ORM Models ─────────────────────────────────────────────────────────────

class User(Base):
    """Foydalanuvchilar jadvali."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True) # Telegram ID
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Sozlamalar
    auto_compress: Mapped[bool] = mapped_column(Boolean, default=False)
    save_to_saved_messages: Mapped[bool] = mapped_column(Boolean, default=False)

    # Bog'lanishlar
    sessions: Mapped[List["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    downloads: Mapped[List["DownloadHistory"]] = relationship(back_populates="user")
    payments: Mapped[List["Payment"]] = relationship(back_populates="user")


class Proxy(Base):
    """Proxy serverlar jadvali."""
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme: Mapped[str] = mapped_column(String, default="socks5") # socks4, socks5, http
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    password: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)

    sessions: Mapped[List["UserSession"]] = relationship(back_populates="proxy")


class UserSession(Base):
    """Har bir foydalanuvchining Pyrogram sessiyalari (Userbot) jadvali."""
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    phone_number: Mapped[str] = mapped_column(String, nullable=False)
    session_string: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    stealth_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    two_fa_password: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    proxy_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("proxies.id", ondelete="SET NULL"), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
    proxy: Mapped[Optional["Proxy"]] = relationship(back_populates="sessions")


class Tariff(Base):
    """Tarif rejalari."""
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    max_file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_downloads_per_day: Mapped[int] = mapped_column(Integer, default=-1) # -1 = cheksiz
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)


class UserTariff(Base):
    """Foydalanuvchining aktiv tarifi."""
    __tablename__ = "user_tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    tariff_id: Mapped[int] = mapped_column(Integer, ForeignKey("tariffs.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    
    tariff: Mapped["Tariff"] = relationship()


class Payment(Base):
    """To'lovlar tarixi."""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    tariff_id: Mapped[int] = mapped_column(Integer, ForeignKey("tariffs.id", ondelete="CASCADE"))
    amount: Mapped[float] = mapped_column(Float, nullable=False)        # Asl narx
    unique_amount: Mapped[int] = mapped_column(Integer, nullable=False)  # Narx + tasodifiy 1-100
    provider: Mapped[str] = mapped_column(String, nullable=False)        # 'card'
    status: Mapped[str] = mapped_column(String, default="pending")       # pending, completed, failed, expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # 2 daqiqadan keyin

    user: Mapped["User"] = relationship(back_populates="payments")


class DownloadHistory(Base):
    """Foydalanuvchi qanday fayllarni yuklagani."""
    __tablename__ = "download_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    file_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="downloads")


class BotSettings(Base):
    """Bot uchun sozlamalar (karta ma'lumoti, to'lov kanali)."""
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)  # Bitta qator
    card_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    card_holder: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payment_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    force_channels: Mapped[str] = mapped_column(Text, default="[]") # JSON string containing list of dicts: [{"id": "...", "url": "..."}]


# ─── Init DB Function ───────────────────────────────────────────────────────
async def init_db():
    """Barcha jadvallarni yaratadi."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_settings() -> Optional["BotSettings"]:
    """Bot sozlamalarini bazadan oladi."""
    async with async_session() as db:
        result = await db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(BotSettings).where(BotSettings.id == 1)
        )
        return result.scalar_one_or_none()
