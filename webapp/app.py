import os
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import ADMIN_ID
from database import ProfitDatabase, db, orders_db, web_db
from handlers.funpay_admin import (
    clean_price,
    extract_order_amount,
    fetch_funpay_sales,
    get_auto_buy_prices,
    make_funpay_account,
)


APP_ROOT = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(APP_ROOT, "templates"))

app = FastAPI(title="Drebolbot Web")
app.mount("/static", StaticFiles(directory=os.path.join(APP_ROOT, "static")), name="static")


@app.exception_handler(HTTPException)
async def ajax_exception_handler(request: Request, exc: HTTPException):
    is_ajax = request.headers.get("x-requested-with", "").lower() == "fetch"
    if is_ajax:
        return JSONResponse(
            {"ok": False, "error": exc.detail or "Ошибка"},
            status_code=exc.status_code,
        )
    if exc.status_code in (303, 302, 301, 307, 308):
        return RedirectResponse(exc.headers.get("Location", "/login"), status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    is_ajax = request.headers.get("x-requested-with", "").lower() == "fetch"
    if is_ajax:
        return JSONResponse(
            {"ok": False, "error": str(exc)[:500]},
            status_code=500,
        )
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


def redirect_to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _login_pair() -> tuple[str, str]:
    return os.getenv("WEB_USERNAME", "admin"), os.getenv("WEB_PASSWORD", str(ADMIN_ID))


def _is_valid_login(username: str, password: str) -> bool:
    good_user, good_pass = _login_pair()
    return secrets.compare_digest(username, good_user) and secrets.compare_digest(password, good_pass)


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


def _list_prime_costs(limit: int = 500):
    if hasattr(orders_db, "list_prime_costs"):
        return orders_db.list_prime_costs(limit)
    orders_db.cursor.execute(
        "SELECT order_id, prime_cost FROM orders_data ORDER BY rowid DESC LIMIT ?",
        (int(limit),),
    )
    return orders_db.cursor.fetchall()


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


def _wants_json(request: Request) -> bool:
    return request.headers.get("x-requested-with", "").lower() == "fetch"


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


def _sale_game(sale) -> str:
    subcategory_name = str(getattr(sale, "subcategory_name", "") or "").strip()
    return subcategory_name.rsplit(",", 1)[0].strip() if subcategory_name else ""


def _sale_date(sale) -> str:
    return str(getattr(sale, "date", getattr(sale, "created_at", "")) or "")


def _order_cards(limit: int = 120, sort: str = "date", mode: str = "all") -> tuple[list[dict], str]:
    gk, ua = db.get_config()
    if not gk:
        return [], "Сначала настройте Golden Key в разделе FunPay."

    try:
        account = make_funpay_account(gk, ua)
        sales = fetch_funpay_sales(account, limit=limit)
    except Exception as exc:
        return [], f"FunPay не отдал заказы: {exc}"

    cards = []
    for sale in sales:
        order_id = str(getattr(sale, "id", ""))
        if not order_id:
            continue
        status_text = str(getattr(sale, "status", "") or "")
        if "refund" in status_text.lower():
            continue

        raw_price = getattr(sale, "price", getattr(sale, "amount", 0))
        sell_price = _money(clean_price(raw_price))
        product_name = getattr(sale, "description", getattr(sale, "product_name", "Без названия"))
        order_game = _sale_game(sale)
        order_date = _sale_date(sale)
        order_amount = extract_order_amount(product_name)
        orders_db.set_order_date(order_id, order_date)
        sell_override = orders_db.get_sell_price(order_id) if hasattr(orders_db, "get_sell_price") else None
        if sell_override is not None:
            sell_price = _money(sell_override)
        cost = orders_db.get_prime_cost(order_id)

        if mode == "unfilled" and cost is not None:
            continue
        if mode == "filled" and cost is None:
            continue

        variants = get_auto_buy_prices(product_name, order_game, order_amount)[:4] if cost is None else []
        profit = (sell_price * 0.97) - _money(cost) if cost is not None else None
        cards.append({
            "id": order_id,
            "status": status_text,
            "date": order_date,
            "game": order_game,
            "product": product_name,
            "sell_price": sell_price,
            "cost": cost,
            "profit": profit,
            "variants": variants,
        })

    if sort == "profit":
        cards.sort(key=lambda item: item["profit"] if item["profit"] is not None else -10**12, reverse=True)
    elif sort == "unfilled":
        cards.sort(key=lambda item: item["cost"] is None, reverse=True)
    return cards, ""


@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": ""})


@app.post("/login")
async def login(request: Request, username: str = Form(""), password: str = Form("")):
    if not _is_valid_login(username.strip(), password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Неверный логин или пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    session_id = web_db.create_session(username.strip() or "admin")
    response = redirect_to("/")
    response.set_cookie("drebol_session", session_id, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@app.post("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("drebol_session", "")
    if session_id:
        web_db.revoke_session(session_id)
    response = redirect_to("/login")
    response.delete_cookie("drebol_session")
    return response


@app.head("/")
async def dashboard_head():
    return Response(status_code=200)


@app.get("/")
async def dashboard(request: Request, user=Depends(require_session)):
    gk, ua = db.get_config()
    profits = _load_admin_profits()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "gk_set": bool(gk),
            "ua_set": bool(ua),
            "all_stats": _all_profit_stats(profits),
            "day_stats": _profit_stats(profits, "day"),
            "week_stats": _profit_stats(profits, "week"),
            "month_stats": _profit_stats(profits, "month"),
        },
    )


@app.get("/funpay")
async def funpay_page(request: Request, user=Depends(require_session)):
    gk, ua = db.get_config()
    return templates.TemplateResponse(
        request=request,
        name="funpay.html",
        context={"user": user, "gk": gk or "", "ua": ua or ""},
    )


@app.post("/funpay/account")
async def update_funpay_account(gk: str = Form(""), ua: str = Form(""), user=Depends(require_session)):
    db.update_config(gk=gk.strip() or None, ua=ua.strip() or None)
    return redirect_to("/funpay")


@app.get("/orders")
async def orders_page(
    request: Request,
    sort: str = "date",
    mode: str = "all",
    limit: int = 120,
    q: str = "",
    user=Depends(require_session),
):
    cards, error = _order_cards(limit=max(10, min(limit, 500)), sort=sort, mode=mode)
    if q.strip():
        query = q.strip().lower()
        query = query.replace("https://funpay.com/orders/", "").replace("http://funpay.com/orders/", "")
        query = query.strip("/").lstrip("#").lower()
        cards = [c for c in cards if query in str(c["id"]).lower()]
    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={
            "user": user,
            "cards": cards,
            "error": error,
            "sort": sort,
            "mode": mode,
            "limit": limit,
            "q": q,
            "stats": _all_profit_stats(_load_admin_profits()),
        },
    )


@app.get("/calc")
async def calc_page(request: Request, user=Depends(require_session)):
    return templates.TemplateResponse(request=request, name="calc.html", context={"user": user})


@app.get("/profits")
async def profits_page(request: Request, page: int = 0, user=Depends(require_session)):
    profits = _load_admin_profits()
    sorted_profits = sorted(profits, key=lambda x: str(x.get("date", "")), reverse=True)
    per_page = 15
    total = len(sorted_profits)
    total_pages = max(1, (total - 1) // per_page + 1)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = start + per_page
    page_items = sorted_profits[start:end]
    all_stats = _all_profit_stats(profits)
    return templates.TemplateResponse(
        request=request,
        name="profits.html",
        context={
            "user": user,
            "profits": page_items,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "all_stats": all_stats,
        },
    )


@app.post("/profits/add")
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


@app.post("/profits/delete")
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


@app.post("/orders/save-cost")
async def save_order_cost(request: Request):
    try:
        session_id = request.cookies.get("drebol_session", "")
        session = web_db.get_session(session_id) if session_id else None
        if not session:
            return JSONResponse({"ok": False, "error": "Сессия истекла. Обновите страницу."}, status_code=401)
        web_db.touch_session(session_id)

        form = await request.form()
        order_id = str(form.get("order_id", ""))
        buy_price = str(form.get("buy_price", "0"))
        sell_price = str(form.get("sell_price", "0"))
        order_date = str(form.get("order_date", ""))

        clean_order_id = order_id.strip().lstrip("#")
        buy = _money(buy_price.replace(",", "."))
        sell = _money(sell_price.replace(",", "."))
        orders_db.set_prime_cost(clean_order_id, buy)
        if hasattr(orders_db, "set_sell_price"):
            orders_db.set_sell_price(clean_order_id, sell, order_date)
        payload = _order_payload(clean_order_id, sell, buy, order_date)
        return JSONResponse(payload)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:500]}, status_code=500)


@app.post("/orders/save-price")
async def save_order_sell_price(request: Request):
    try:
        session_id = request.cookies.get("drebol_session", "")
        session = web_db.get_session(session_id) if session_id else None
        if not session:
            return JSONResponse({"ok": False, "error": "Сессия истекла. Обновите страницу."}, status_code=401)
        web_db.touch_session(session_id)

        form = await request.form()
        order_id = str(form.get("order_id", ""))
        sell_price = str(form.get("sell_price", "0"))
        buy_price = str(form.get("buy_price", ""))
        order_date = str(form.get("order_date", ""))

        clean_order_id = order_id.strip().lstrip("#")
        sell = _money(sell_price.replace(",", "."))
        existing_buy = orders_db.get_prime_cost(clean_order_id)
        buy = _money(buy_price.replace(",", ".")) if str(buy_price or "").strip() else _money(existing_buy)
        if hasattr(orders_db, "set_sell_price"):
            orders_db.set_sell_price(clean_order_id, sell, order_date)
        if str(buy_price or "").strip():
            orders_db.set_prime_cost(clean_order_id, buy)
        if buy > 0 or existing_buy is not None:
            payload = _order_payload(clean_order_id, sell, buy, order_date)
        else:
            payload = {
                "ok": True,
                "order_id": clean_order_id,
                "buy_price": None,
                "sell_price": round(sell, 2),
                "profit": None,
                "profit_label": "— ₽",
                "buy_label": "0.00 ₽",
                "sell_label": f"{sell:.2f} ₽",
            }
        return JSONResponse(payload)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:500]}, status_code=500)


@app.get("/settings")
async def settings_page(request: Request, user=Depends(require_session)):
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "user": user,
            "sessions": web_db.list_sessions(),
            "current_session": user["session_id"],
            "login_username": _login_pair()[0],
        },
    )


@app.post("/settings/revoke")
async def revoke_session(session_id: str = Form(...), user=Depends(require_session)):
    web_db.revoke_session(session_id)
    if session_id == user["session_id"]:
        response = redirect_to("/login")
        response.delete_cookie("drebol_session")
        return response
    return redirect_to("/settings")
