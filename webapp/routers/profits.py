from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request

from config import ADMIN_ID
from database import ProfitDatabase

from .shared import (
    _load_admin_profits,
    _money,
    _parse_date,
    redirect_to,
    require_session,
    templates,
)

router = APIRouter(prefix="/profits")


@router.get("")
async def profits_page(request: Request, period: str = "day", page: int = 0, date_from: str = "", date_to: str = "", sort: str = "date", user=Depends(require_session)):
    try:
        profits = _load_admin_profits()
    except Exception:
        profits = []
    now = datetime.now()

    if date_from or date_to:
        start = None
        end = None
        try:
            if date_from:
                start = datetime.strptime(date_from, "%d.%m.%Y")
        except ValueError:
            pass
        try:
            if date_to:
                end = datetime.strptime(date_to, "%d.%m.%Y").replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError:
            pass
        label_from = date_from or "..."
        label_to = date_to or "..."
        period_label = f"{label_from} — {label_to}"
        period = "custom"
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = None
        period_label = "Неделя"
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = None
        period_label = "Месяц"
    elif period == "all":
        start = None
        end = None
        period_label = "Всё время"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = None
        period = "day"
        period_label = "Сегодня"

    filtered = []
    for p in profits:
        dt = _parse_date(p.get("date", ""))
        if start and dt and dt < start:
            continue
        if end and dt and dt > end:
            continue
        filtered.append(p)

    if sort == "profit_desc":
        filtered.sort(key=lambda x: float(x.get("profit") or 0), reverse=True)
    elif sort == "profit_asc":
        filtered.sort(key=lambda x: float(x.get("profit") or 0))
    else:
        filtered.sort(key=lambda x: str(x.get("date", "")), reverse=True)

    per_page = 15
    total = len(filtered)
    total_pages = max(1, (total - 1) // per_page + 1)
    page = max(0, min(page, total_pages - 1))
    page_items = filtered[page * per_page : (page + 1) * per_page]

    stats = {
        "count": len(filtered),
        "sell": sum(_money(p.get("sell_price")) for p in filtered),
        "profit": sum(_money(p.get("profit")) for p in filtered),
    }

    return templates.TemplateResponse(
        request=request,
        name="profits.html",
        context={
            "user": user,
            "profits": page_items,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "stats": stats,
            "period": period,
            "period_label": period_label,
            "sort": sort,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@router.post("/add")
async def add_profit(request: Request, user=Depends(require_session)):
    try:
        form = await request.form()
        ptype = str(form.get("type", "")).strip()
        buy = _money(str(form.get("buy_price", "0")).replace(",", "."))
        sell = _money(str(form.get("sell_price", "0")).replace(",", "."))
        profit_val = round((sell * 0.97) - buy, 2)
        date = str(form.get("date", "")) or datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        if not ptype:
            return redirect_to("/profits")
        profit_db = ProfitDatabase(ADMIN_ID)
        profits = profit_db.load_profits()
        profits.append({
            "type": ptype,
            "buy_price": round(buy, 2),
            "sell_price": round(sell, 2),
            "profit": profit_val,
            "date": date,
        })
        profit_db.save_profits(profits)
    except Exception as e:
        print(f"[PROFIT ADD ERROR] {e}")
    return redirect_to("/profits")


@router.post("/delete")
async def delete_profit(request: Request, user=Depends(require_session)):
    try:
        form = await request.form()
        idx = int(str(form.get("index", "-1")))
        profit_db = ProfitDatabase(ADMIN_ID)
        profits = profit_db.load_profits()
        sorted_profits = sorted(profits, key=lambda x: str(x.get("date", "")), reverse=True)
        if 0 <= idx < len(sorted_profits):
            to_remove = sorted_profits[idx]
            for i, p in enumerate(profits):
                if (p.get("type") == to_remove.get("type") and
                    p.get("date") == to_remove.get("date") and
                    p.get("buy_price") == to_remove.get("buy_price")):
                    profits.pop(i)
                    break
            profit_db.save_profits(profits)
    except Exception as e:
        print(f"[PROFIT DELETE ERROR] {e}")
    return redirect_to("/profits")
