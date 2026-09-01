from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request

from database import db, orders_db

from handlers.funpay_admin import clean_price, fetch_funpay_sales, make_funpay_account

from .shared import _money, require_session, templates

router = APIRouter()


@router.get("/tasks")
async def tasks_page(request: Request, period: str = "day", user=Depends(require_session)):
    gk, ua = db.get_config()
    error = ""
    cards = []
    period_label = ""
    if gk:
        try:
            account = make_funpay_account(gk, ua)
            sales = fetch_funpay_sales(account, limit=300)
            now = datetime.now()
            if period == "week":
                start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
                period_label = "Неделя"
            elif period == "month":
                start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                period_label = "Месяц"
            else:
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                period_label = "День"

            for sale in sales:
                order_id = str(getattr(sale, "id", ""))
                if not order_id:
                    continue
                status_text = str(getattr(sale, "status", "") or "")
                if "refund" in status_text.lower():
                    continue
                if orders_db.get_prime_cost(order_id) is not None:
                    continue
                raw_date = str(getattr(sale, "date", getattr(sale, "created_at", "")) or "")
                dt = None
                for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
                    try:
                        dt = datetime.strptime(raw_date[:19], fmt)
                        break
                    except Exception:
                        continue
                if dt and dt < start:
                    continue
                raw_price = getattr(sale, "price", getattr(sale, "amount", 0))
                sell_price = _money(clean_price(raw_price))
                product_name = getattr(sale, "description", getattr(sale, "product_name", ""))
                cards.append({"id": order_id, "product": product_name, "sell_price": sell_price, "date": raw_date})
        except Exception as exc:
            error = f"Ошибка: {exc}"
    else:
        error = "Golden Key не настроен"

    return templates.TemplateResponse(request=request, name="tasks.html", context={
        "user": user, "cards": cards, "error": error, "period": period,
        "period_label": period_label,
    })
