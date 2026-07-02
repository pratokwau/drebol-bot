import os
import json
from datetime import datetime


def format_date_now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def load_profits(user_id: int = None) -> list:
    if user_id:
        path = f"data/profits_{user_id}.json"
    else:
        path = "data/profits.json"
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_profits(profits: list, user_id: int = None):
    if user_id:
        path = f"data/profits_{user_id}.json"
    else:
        path = "data/profits.json"
    os.makedirs("data", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profits, f, ensure_ascii=False, indent=2)


def load_inventory(user_id: int = None) -> dict:
    if user_id:
        path = f"data/inventory_{user_id}.json"
    else:
        path = "data/inventory.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_inventory(inventory: dict, user_id: int = None):
    if user_id:
        path = f"data/inventory_{user_id}.json"
    else:
        path = "data/inventory.json"
    os.makedirs("data", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, ensure_ascii=False, indent=2)
