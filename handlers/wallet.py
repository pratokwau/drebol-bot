import os
import json
import html
from datetime import datetime, timedelta
from aiogram import Router, types
from aiogram.enums import ParseMode
from handlers.utils import load_profits
from config import ADMIN_ID

router = Router()

def update_wallet(user_id: int, amount: float):
    """
    Заглушка: теперь баланс считается динамически из профитов.
    Функция оставлена для совместимости с другими файлами.
    """
    return 0

def _parse_profit_date(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _day_bounds(day: datetime) -> tuple[datetime, datetime]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


def _money(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


async def send_daily_report(bot, user_id, report_date: datetime | None = None):
    profits = load_profits(user_id)
    report_day = report_date or datetime.now()
    start, end = _day_bounds(report_day)
    day_label = report_day.strftime("%d.%m.%Y")
    
    fp_orders = [
        p for p in profits 
        if (dt := _parse_profit_date(p.get("date", ""))) is not None
        and start <= dt <= end
        and (p.get('type') == "FunPay" or "FP #" in p.get('type', ''))
    ]
    fp_count = len(fp_orders)
    fp_sum = sum(_money(p.get('sell_price', 0)) for p in fp_orders)

    po_orders = [
        p for p in profits 
        if (dt := _parse_profit_date(p.get("date", ""))) is not None
        and start <= dt <= end
        and (p.get('type') == "PlayerOK" or "PO #" in p.get('type', ''))
    ]
    po_count = len(po_orders)
    po_sum = sum(_money(p.get('sell_price', 0)) for p in po_orders)

    total_profit = sum(
        _money(p.get('profit', 0))
        for p in profits
        if (dt := _parse_profit_date(p.get("date", ""))) is not None
        and start <= dt <= end
        and _money(p.get("profit", 0)) > 0
    )

    text = (
        f"🌙 <b>Статистика за {day_label}</b>\n\n"
        f"📦 Продажи FunPay: <b>{fp_count}</b> на сумму <b>{fp_sum:.2f} ₽</b>\n"
        f"📦 Продажи PlayerOK: <b>{po_count}</b> на сумму <b>{po_sum:.2f} ₽</b>\n"
        f"──────────────────\n"
        f"💰 Чистая прибыль: <b>{total_profit:.2f} ₽</b>"
    )

    # Блок про изменения коэффициентов СБП - только для админа
    if int(user_id) == int(ADMIN_ID):
        try:
            from handlers.minprice import load_sbp_changes_today
            sbp_data = load_sbp_changes_today()
            text += "\n\n📈 <b>Коэффициенты СБП за день</b>\n"
            if not sbp_data:
                text += "\n⚠️ <i>Нет свежих данных проверки СБП.</i>"
            else:
                changes = sbp_data.get("changes", [])
                unchanged = sbp_data.get("unchanged", [])
                errors = sbp_data.get("errors", [])

                if changes:
                    text += f"\n🔄 <b>Изменились ({len(changes)}):</b>\n"
                    for ch in changes:
                        name = html.escape(ch["name"])
                        old = ch["old"]
                        new = ch["new"]
                        arrow = "⬆️" if new > old else "⬇️"
                        text += f"  {arrow} {name}: ×{old:.4f} → ×{new:.4f}\n"
                else:
                    text += "\n✅ <i>За день не изменились</i>\n"

                text += f"\n📊 Без изменений: <b>{len(unchanged)}</b>"
        except Exception as e:
            print(f"[DAILY REPORT] Ошибка загрузки SBP изменений: {e}")
            text += "\n\n📈 <b>Коэффициенты СБП за день</b>\n⚠️ <i>Ошибка загрузки данных проверки.</i>"

    await bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
