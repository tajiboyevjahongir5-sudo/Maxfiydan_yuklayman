import json
import os
import time
from pathlib import Path

STORAGE_FILE = "db.json"

def _load_data() -> dict:
    if not os.path.exists(STORAGE_FILE):
        return {"logs": [], "users": {}}
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {"logs": [], "users": {}}

def _save_data(data: dict) -> None:
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_log(user_id: int, user_name: str, link: str, status: str, error_msg: str = "") -> None:
    data = _load_data()
    
    # Update user stats
    user_id_str = str(user_id)
    if user_id_str not in data["users"]:
        data["users"][user_id_str] = {
            "name": user_name,
            "requests": 0,
            "first_seen": time.time(),
            "last_seen": time.time(),
            "banned": False
        }
    data["users"][user_id_str]["requests"] += 1
    data["users"][user_id_str]["last_seen"] = time.time()
    
    # Add log entry
    log_entry = {
        "id": int(time.time() * 1000),
        "time": time.time(),
        "user_id": user_id,
        "user_name": user_name,
        "link": link,
        "status": status,
        "error_msg": error_msg
    }
    data["logs"].insert(0, log_entry)
    
    # Keep only last 200 logs
    if len(data["logs"]) > 200:
        data["logs"] = data["logs"][:200]
        
    _save_data(data)

def get_stats() -> dict:
    data = _load_data()
    logs = data.get("logs", [])
    users = data.get("users", {})
    
    success_count = sum(1 for log in logs if log.get("status") == "success")
    error_count = sum(1 for log in logs if log.get("status") != "success")
    
    return {
        "total_requests": len(logs),
        "success": success_count,
        "errors": error_count,
        "active_users": len(users)
    }

def get_logs(limit: int = 50) -> list:
    data = _load_data()
    return data.get("logs", [])[:limit]

def get_users() -> dict:
    data = _load_data()
    return data.get("users", {})

def toggle_ban_user(user_id: int) -> bool:
    data = _load_data()
    user_id_str = str(user_id)
    if user_id_str in data["users"]:
        current_status = data["users"][user_id_str].get("banned", False)
        data["users"][user_id_str]["banned"] = not current_status
        _save_data(data)
        return not current_status
    return False

def is_user_banned(user_id: int) -> bool:
    data = _load_data()
    user_id_str = str(user_id)
    if user_id_str in data["users"]:
        return data["users"][user_id_str].get("banned", False)
    return False
