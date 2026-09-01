import os
import secrets
import json
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from config import ADMIN_ID
from database import ProfitDatabase, db, orders_db, web_db

APP_ROOT = os.path.dirname(os.path.dirname(__file__))
templates = Jinja2Templates(directory=os.path.join(APP_ROOT, "templates"))

NOTIFICATIONS_FILE = "data/notifications.json"


def redirect_to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _login_pair() -> tuple[str, str]:
    return os.getenv("WEB_USERNAME", "admin"), os.getenv("WEB_PASSWORD", str(ADMIN_ID))


def _is_valid_login(username: str, password: str) -> bool:
    good_user, good_pass = _login_pair()
    if secrets.compare_digest(username, good_user) and secrets.compare_digest(password, good_pass):
        return True
    if hasattr(web_db, "check_account") and web_db.check_account(username, password):
        return True
    return False


def require_session(request: Request):
    session_id = request.cookies.get("drebol_session", "")
    session = web_db.get_session(session_id) if session_id else None
    if not session:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    web_db.touch_session(session_id)
    return {"session_id": session[0], "username": session[1]}


def _money(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(value: str) -> datetime | None:
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _period_bounds(period: str) -> tuple[datetime, datetime, str]:
    now = datetime.now()
    if period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start, end, "Неделя"
    if period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start, end, "Месяц"
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end, "День"


def _profit_stats(profits: list[dict], period: str = "day") -> dict:
    start, end, label = _period_bounds(period)
    selected = []
    for item in profits:
        dt = _parse_date(item.get("date", ""))
        if dt and start <= dt <= end:
            selected.append(item)
    return {
        "label": label,
        "count": len(selected),
        "sell": sum(_money(p.get("sell_price")) for p in selected),
        "profit": sum(_money(p.get("profit")) for p in selected),
    }


def _all_profit_stats(profits: list[dict]) -> dict:
    return {
        "label": "Всего",
        "count": len(profits),
        "sell": sum(_money(p.get("sell_price")) for p in profits),
        "profit": sum(_money(p.get("profit")) for p in profits),
    }


def _load_admin_profits() -> list[dict]:
    return ProfitDatabase(ADMIN_ID).load_profits()


def _wants_json(request: Request) -> bool:
    return request.headers.get("x-requested-with", "").lower() == "fetch"


def _sale_game(sale) -> str:
    subcategory_name = str(getattr(sale, "subcategory_name", "") or "").strip()
    return subcategory_name.rsplit(",", 1)[0].strip() if subcategory_name else ""


def _sale_date(sale) -> str:
    return str(getattr(sale, "date", getattr(sale, "created_at", "")) or "")


# --- Notifications ---

def _load_notifications() -> list:
    if not os.path.exists(NOTIFICATIONS_FILE):
        return []
    try:
        with open(NOTIFICATIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_notifications(data: list):
    os.makedirs("data", exist_ok=True)
    with open(NOTIFICATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_notification(text: str, ntype: str = "info"):
    notifs = _load_notifications()
    notifs.insert(0, {"text": text, "type": ntype, "time": datetime.now().strftime("%d.%m.%Y %H:%M"), "read": False})
    _save_notifications(notifs[:200])


def _unread_count() -> int:
    return sum(1 for n in _load_notifications() if not n.get("read"))


def _mark_all_read():
    notifs = _load_notifications()
    changed = False
    for n in notifs:
        if not n.get("read"):
            n["read"] = True
            changed = True
    if changed:
        _save_notifications(notifs)


def _notif_count() -> int:
    try:
        return len(_load_notifications())
    except Exception:
        return 0


def _notif_count_safe() -> int:
    try:
        if os.path.exists(NOTIFICATIONS_FILE):
            with open(NOTIFICATIONS_FILE, encoding="utf-8") as f:
                return sum(1 for n in json.load(f) if not n.get("read"))
    except Exception:
        pass
    return 0


def _save_profit_from_order(order_id: str, sell_price: float, buy_price: float, order_date: str):
    profit_db = ProfitDatabase(ADMIN_ID)
    profits = profit_db.load_profits()
    net_profit = (sell_price * 0.97) - buy_price
    existing_idx = next(
        (i for i, item in enumerate(profits) if f"FP #{order_id}" in str(item.get("type", ""))),
        None,
    )
    entry = {
        "type": f"FP #{order_id} (WEB)",
        "buy_price": round(buy_price, 2),
        "sell_price": round(sell_price, 2),
        "profit": round(net_profit, 2),
        "date": order_date or datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    }
    if existing_idx is None:
        profits.append(entry)
    else:
        old_date = profits[existing_idx].get("date", "")
        entry["date"] = order_date or old_date or entry["date"]
        profits[existing_idx] = entry
    profit_db.save_profits(profits)
    return entry


def _order_payload(order_id: str, sell_price: float, buy_price: float, order_date: str) -> dict:
    entry = _save_profit_from_order(order_id, sell_price, buy_price, order_date)
    return {
        "ok": True,
        "order_id": order_id,
        "buy_price": round(buy_price, 2),
        "sell_price": round(sell_price, 2),
        "profit": entry["profit"],
        "profit_label": f'{entry["profit"]:.2f} ₽',
        "buy_label": f"{buy_price:.2f} ₽",
        "sell_label": f"{sell_price:.2f} ₽",
    }
