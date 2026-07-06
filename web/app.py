import os
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from datetime import timedelta
from web.auth import Token, create_access_token, get_current_admin, ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY
from web.storage import get_stats, get_logs, get_users, toggle_ban_user
from web.api.sessions import router as sessions_router
from web.api.auth import router as auth_router
from web.api.billing import router as billing_router
from web.api.user import router as user_router
from web.api.admin import router as admin_router

app = FastAPI(title="Telegram Media Bot SaaS")

# Include routers
app.include_router(sessions_router)
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(user_router)
app.include_router(admin_router)

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/user-dashboard")
async def read_user_dashboard():
    return FileResponse(os.path.join(static_dir, "user_dashboard.html"))

@app.get("/admin-panel")
async def read_admin():
    return FileResponse(os.path.join(static_dir, "admin.html"))

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    # We only have one admin user. Username is "admin", password is ADMIN_SECRET from .env
    if form_data.username != "admin" or form_data.password != SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": form_data.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/stats")
async def api_stats(current_user: str = Depends(get_current_admin)):
    from database import async_session, User, DownloadHistory
    from sqlalchemy import select, func
    from web.storage import get_stats as get_json_stats
    
    # Bizda logs asosan JSON da turibdi (chunki barcha xatolar va requestlar loglanadi)
    # Ammo Userlar bazada. Shuning uchun JSON dagi stats ni bazadagi user count bilan birlashtiramiz
    json_stats = get_json_stats()
    
    async with async_session() as db:
        users_count = await db.execute(select(func.count(User.id)))
        active_users = users_count.scalar() or 0
        
    json_stats["active_users"] = active_users
    return json_stats

@app.get("/api/logs")
async def api_logs(limit: int = 50, current_user: str = Depends(get_current_admin)):
    from web.storage import get_logs
    return get_logs(limit)

@app.get("/api/users")
async def api_users(current_user: str = Depends(get_current_admin)):
    from database import async_session, User, DownloadHistory
    from sqlalchemy import select, func
    async with async_session() as db:
        # Get users
        result = await db.execute(select(User))
        users_db = result.scalars().all()
        
        # Get request count for each user
        counts_res = await db.execute(
            select(DownloadHistory.user_id, func.count(DownloadHistory.id))
            .group_by(DownloadHistory.user_id)
        )
        counts = {row[0]: row[1] for row in counts_res.all()}
        
        users_dict = {}
        for u in users_db:
            users_dict[str(u.id)] = {
                "name": u.first_name,
                "requests": counts.get(u.id, 0),
                "banned": u.is_banned
            }
        return users_dict

@app.post("/api/users/{user_id}/toggle_ban")
async def api_toggle_ban(user_id: int, current_user: str = Depends(get_current_admin)):
    from database import async_session, User
    from sqlalchemy import select
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        user.is_banned = not user.is_banned
        await db.commit()
        return {"status": "success", "banned": user.is_banned}

@app.get("/api/config")
async def api_config(current_user: str = Depends(get_current_admin)):
    return {
        "MAX_FILE_SIZE_MB": os.getenv("MAX_FILE_SIZE_MB", "2000"),
        "DOWNLOAD_DIR": os.getenv("DOWNLOAD_DIR", "./downloads"),
        "ALLOWED_USERS": os.getenv("ALLOWED_USERS", "")
    }
