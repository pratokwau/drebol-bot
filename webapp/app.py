import os
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import ADMIN_ID
from database import ProfitDatabase, db, orders_db


APP_ROOT = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(APP_ROOT, "templates"))
security = HTTPBasic()

app = FastAPI(title="Drebolbot Web")
app.mount("/static", StaticFiles(directory=os.path.join(APP_ROOT, "static")), name="static")


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    username = os.getenv("WEB_USERNAME", "admin")
    password = os.getenv("WEB_PASSWORD", str(ADMIN_ID))
    ok_user = secrets.compare_digest(credentials.username, username)
    ok_pass = secrets.compare_digest(credentials.password, password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def redirect_to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


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


def _load_admin_profits() -> list[dict]:
    return ProfitDatabase(ADMIN_ID).load_profits()


@app.get("/")
async def dashboard(request: Request, _: str = Depends(require_auth)):
    gk, ua = db.get_config()
    profits = _load_admin_profits()
    orders = orders_db.list_prime_costs(12)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "gk_set": bool(gk),
            "ua_set": bool(ua),
            "orders": orders,
            "total_profits": len(profits),
            "day_stats": _profit_stats(profits, "day"),
            "month_stats": _profit_stats(profits, "month"),
        },
    )


@app.get("/funpay")
async def funpay_page(request: Request, _: str = Depends(require_auth)):
    gk, ua = db.get_config()
    return templates.TemplateResponse(
        "funpay.html",
        {
            "request": request,
            "gk": gk or "",
            "ua": ua or "",
            "orders": orders_db.list_prime_costs(200),
        },
    )


@app.post("/funpay/account")
async def update_funpay_account(
    gk: str = Form(""),
    ua: str = Form(""),
    _: str = Depends(require_auth),
):
    db.update_config(gk=gk.strip() or None, ua=ua.strip() or None)
    return redirect_to("/funpay")


@app.post("/funpay/orders")
async def update_order_cost(
    order_id: str = Form(...),
    prime_cost: str = Form(...),
    _: str = Depends(require_auth),
):
    orders_db.set_prime_cost(order_id.strip().lstrip("#"), _money(prime_cost.replace(",", ".")))
    return redirect_to("/funpay")


@app.post("/funpay/orders/delete")
async def delete_order_cost(order_id: str = Form(...), _: str = Depends(require_auth)):
    orders_db.delete_prime_cost(order_id.strip().lstrip("#"))
    return redirect_to("/funpay")


@app.get("/profits")
async def profits_page(request: Request, period: str = "day", _: str = Depends(require_auth)):
    profits = _load_admin_profits()
    return templates.TemplateResponse(
        "profits.html",
        {
            "request": request,
            "profits": list(reversed(list(enumerate(profits))))[:200],
            "stats": _profit_stats(profits, period),
            "period": period,
            "now_text": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        },
    )


@app.post("/profits/add")
async def add_profit(
    profit_type: str = Form("FunPay"),
    buy_price: str = Form("0"),
    sell_price: str = Form("0"),
    profit: str = Form(""),
    date: str = Form(""),
    _: str = Depends(require_auth),
):
    profit_db = ProfitDatabase(ADMIN_ID)
    profits = profit_db.load_profits()
    buy = _money(buy_price.replace(",", "."))
    sell = _money(sell_price.replace(",", "."))
    clean_profit = _money(profit.replace(",", ".")) if profit.strip() else (sell * 0.97) - buy
    profits.append({
        "type": profit_type.strip() or "FunPay",
        "buy_price": round(buy, 2),
        "sell_price": round(sell, 2),
        "profit": round(clean_profit, 2),
        "date": date.strip() or datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    })
    profit_db.save_profits(profits)
    return redirect_to("/profits")


@app.post("/profits/delete")
async def delete_profit(index: int = Form(...), _: str = Depends(require_auth)):
    profit_db = ProfitDatabase(ADMIN_ID)
    profits = profit_db.load_profits()
    if 0 <= index < len(profits):
        profits.pop(index)
        profit_db.save_profits(profits)
    return redirect_to("/profits")
