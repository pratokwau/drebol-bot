import os
import json
from datetime import datetime

from database import ProfitDatabase


def format_date_now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def load_profits(user_id: int = None) -> list:
    user_id = int(user_id or 0)
    db = ProfitDatabase(user_id)
    rows = db.load_profits()
    if rows:
        return rows

    legacy_paths = [
        f"data/profits_{user_id}.json",
        "data/profits.json",
    ]
    for path in legacy_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                legacy_rows = json.load(f)
            if isinstance(legacy_rows, list):
                db.save_profits(legacy_rows)
                return legacy_rows
        except Exception:
            continue
    return []


def save_profits(profits: list, user_id: int = None):
    if isinstance(profits, int) and isinstance(user_id, list):
        profits, user_id = user_id, profits

    user_id = int(user_id or 0)
    db = ProfitDatabase(user_id)
    db.save_profits(profits)


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
