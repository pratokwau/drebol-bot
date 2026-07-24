import os
import secrets
import hashlib
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
from handlers.minprice import (
    load_mp as _load_mp,
    save_mp as _save_mp,
    calc_min_price as _calc_min_price,
    get_hash as _mp_hash,
    get_items as _mp_items,
    get_game_meta as _mp_meta,
    set_game_meta as _mp_set_meta,
    get_item_offer_ids as _mp_offer_ids,
    CASHBACK_OPTIONS as _CASHBACK_OPTS,
)


APP_ROOT = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(APP_ROOT, "templates"))

app = FastAPI(title="Drebolbot Web", version="2.1")
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

    fetch_limit = limit
    if mode == "unfilled":
        fetch_limit = min(limit * 4, 5000)
    elif mode == "filled":
        fetch_limit = min(limit * 2, 5000)

    try:
        account = make_funpay_account(gk, ua)
        sales = fetch_funpay_sales(account, limit=fetch_limit)
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
        if len(cards) >= limit:
            break

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
async def profits_page(request: Request, period: str = "day", page: int = 0, user=Depends(require_session)):
    print(f"[DEBUG] /profits called by {user.get('username')} period={period} page={page}")
    try:
        profits = _load_admin_profits()
        print(f"[DEBUG] loaded {len(profits)} profit records")
    except Exception as e:
        print(f"[DEBUG] _load_admin_profits error: {e}")
        profits = []
    now = datetime.now()
    if period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        period_label = "Неделя"
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_label = "Месяц"
    elif period == "all":
        start = None
        period_label = "Всё время"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        period = "day"
        period_label = "Сегодня"

    filtered = []
    for p in profits:
        dt = _parse_date(p.get("date", ""))
        if start is None or (dt and dt >= start):
            filtered.append(p)
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

    print(f"[DEBUG] rendering profits.html with {len(page_items)} items")
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
    from handlers.settings import get_user_settings
    sessions = web_db.list_sessions()
    revoked_count = sum(1 for s in sessions if s[4])
    settings = get_user_settings(ADMIN_ID)
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "user": user,
            "sessions": sessions,
            "current_session": user["session_id"],
            "login_username": _login_pair()[0],
            "revoked_count": revoked_count,
            "settings": settings,
        },
    )


@app.post("/settings/update-bot")
async def settings_update_bot(request: Request, user=Depends(require_session)):
    from handlers.settings import update_setting
    form = await request.form()
    update_setting(ADMIN_ID, "restart_notify", "1" in str(form.get("restart_notify", "")))
    update_setting(ADMIN_ID, "admin_report_notify", "1" in str(form.get("admin_report_notify", "")))
    time_val = str(form.get("admin_report_time", "23:59")).strip()
    if time_val:
        update_setting(ADMIN_ID, "admin_report_time", time_val)
    return redirect_to("/settings")


@app.post("/settings/revoke")
async def revoke_session(session_id: str = Form(...), user=Depends(require_session)):
    web_db.revoke_session(session_id)
    if session_id == user["session_id"]:
        response = redirect_to("/login")
        response.delete_cookie("drebol_session")
        return response
    return redirect_to("/settings")


@app.post("/settings/revoke-all")
async def revoke_all_sessions(user=Depends(require_session)):
    sessions = web_db.list_sessions()
    for session_id, _, _, _, revoked in sessions:
        if revoked and session_id != user["session_id"]:
            web_db.delete_session(session_id)
    return redirect_to("/settings")


@app.post("/settings/update")
async def settings_update(user=Depends(require_session)):
    import subprocess
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, cwd=os.path.dirname(__file__))
        output = result.stdout.strip() + result.stderr.strip()
        return JSONResponse({"ok": True, "output": output[:500]})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)

@app.get("/minprice")
async def minprice_page(request: Request, user=Depends(require_session)):
    mp = _load_mp(ADMIN_ID)
    games = []
    for game_name in sorted(mp.keys()):
        items = _mp_items(mp, game_name)
        meta = _mp_meta(mp, game_name)
        unique_names = set()
        linked_names = set()
        for _, info in items.items():
            if not isinstance(info, dict):
                continue
            name = info.get("name", "")
            unique_names.add(name)
            if _mp_offer_ids(info):
                linked_names.add(name)
        games.append({
            "name": game_name,
            "hash": _mp_hash(game_name),
            "items_count": len(unique_names),
            "linked_count": len(linked_names),
            "sbp_rate": meta.get("sbp_rate"),
        })
    return templates.TemplateResponse(request=request, name="minprice.html", context={
        "user": user, "games": games, "total_games": len(games),
    })


@app.post("/minprice/add-game")
async def minprice_add_game(request: Request, user=Depends(require_session)):
    form = await request.form()
    game_name = str(form.get("game_name", "")).strip()
    if game_name:
        mp = _load_mp(ADMIN_ID)
        if game_name not in mp:
            mp[game_name] = {}
        _save_mp(ADMIN_ID, mp)
    return redirect_to("/minprice")


@app.post("/minprice/delete-game")
async def minprice_delete_game(request: Request, user=Depends(require_session)):
    form = await request.form()
    game_name = str(form.get("game_name", "")).strip()
    if game_name:
        mp = _load_mp(ADMIN_ID)
        if game_name in mp:
            del mp[game_name]
            _save_mp(ADMIN_ID, mp)
    return redirect_to("/minprice")


@app.get("/minprice/game/{game_hash}")
async def minprice_game_page(request: Request, game_hash: str, page: int = 0, user=Depends(require_session)):
    mp = _load_mp(ADMIN_ID)
    game_name = None
    for name in mp.keys():
        if _mp_hash(name) == game_hash:
            game_name = name
            break
    if not game_name:
        return redirect_to("/minprice")

    meta = _mp_meta(mp, game_name)
    items = _mp_items(mp, game_name)
    sbp_rate = meta.get("sbp_rate")

    groups = {}
    for item_id, info in items.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name", item_id)
        if name not in groups:
            groups[name] = []
        groups[name].append({"id": item_id, **info})

    sorted_groups = sorted(groups.items(), key=lambda x: x[0].lower())
    per_page = 20
    total = len(sorted_groups)
    total_pages = max(1, (total - 1) // per_page + 1)
    page = max(0, min(page, total_pages - 1))
    page_groups = sorted_groups[page * per_page:(page + 1) * per_page]

    return templates.TemplateResponse(request=request, name="minprice_game.html", context={
        "user": user,
        "game_name": game_name,
        "game_hash": game_hash,
        "groups": page_groups,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "sbp_rate": sbp_rate,
        "cashback_options": _CASHBACK_OPTS,
        "offer_link": lambda oid: f"https://funpay.com/lots/offer?id={oid}",
    })


@app.post("/minprice/game/{game_hash}/add")
async def minprice_add_item(request: Request, game_hash: str, user=Depends(require_session)):
    mp = _load_mp(ADMIN_ID)
    game_name = None
    for name in mp.keys():
        if _mp_hash(name) == game_hash:
            game_name = name
            break
    if not game_name:
        return redirect_to("/minprice")

    form = await request.form()
    item_name = str(form.get("item_name", "")).strip()
    cost_no = _money(str(form.get("cost_no", "0")).replace(",", "."))
    cost_yes = _money(str(form.get("cost_yes", "0")).replace(",", "."))

    if not item_name:
        return redirect_to(f"/minprice/game/{game_hash}")

    if game_name not in mp:
        mp[game_name] = {}

    uid = hashlib.md5(f"{item_name}_no_{secrets.token_hex(4)}".encode()).hexdigest()[:8]
    mp[game_name][uid] = {
        "name": item_name, "cost": cost_no, "min_price": _calc_min_price(cost_no), "cashback": "no",
    }

    if cost_yes > 0:
        uid_yes = hashlib.md5(f"{item_name}_yes_{secrets.token_hex(4)}".encode()).hexdigest()[:8]
        mp[game_name][uid_yes] = {
            "name": item_name, "cost": cost_yes, "min_price": _calc_min_price(cost_yes), "cashback": "yes",
        }

    _save_mp(ADMIN_ID, mp)
    return redirect_to(f"/minprice/game/{game_hash}")


@app.post("/minprice/game/{game_hash}/edit/{item_id}")
async def minprice_edit_item(request: Request, game_hash: str, item_id: str, user=Depends(require_session)):
    mp = _load_mp(ADMIN_ID)
    game_name = None
    for name in mp.keys():
        if _mp_hash(name) == game_hash:
            game_name = name
            break
    if not game_name or item_id not in mp.get(game_name, {}):
        return redirect_to(f"/minprice/game/{game_hash}")

    form = await request.form()
    new_name = str(form.get("item_name", "")).strip()
    new_cost = _money(str(form.get("cost", "0")).replace(",", "."))
    new_cashback = str(form.get("cashback", "none")).strip()

    if new_name:
        mp[game_name][item_id]["name"] = new_name
    if new_cost > 0:
        mp[game_name][item_id]["cost"] = new_cost
        mp[game_name][item_id]["min_price"] = _calc_min_price(new_cost)
    if new_cashback in ("yes", "no", "none"):
        mp[game_name][item_id]["cashback"] = new_cashback

    _save_mp(ADMIN_ID, mp)
    return redirect_to(f"/minprice/game/{game_hash}")


@app.post("/minprice/game/{game_hash}/delete")
async def minprice_delete_items(request: Request, game_hash: str, user=Depends(require_session)):
    mp = _load_mp(ADMIN_ID)
    game_name = None
    for name in mp.keys():
        if _mp_hash(name) == game_hash:
            game_name = name
            break
    if not game_name:
        return redirect_to("/minprice")

    form = await request.form()
    item_id = str(form.get("item_id", "")).strip()
    if item_id and item_id in mp.get(game_name, {}):
        del mp[game_name][item_id]
        _save_mp(ADMIN_ID, mp)
    return redirect_to(f"/minprice/game/{game_hash}")


@app.post("/minprice/game/{game_hash}/sbp")
async def minprice_set_sbp(request: Request, game_hash: str, user=Depends(require_session)):
    mp = _load_mp(ADMIN_ID)
    game_name = None
    for name in mp.keys():
        if _mp_hash(name) == game_hash:
            game_name = name
            break
    if not game_name:
        return redirect_to("/minprice")

    form = await request.form()
    rate = _money(str(form.get("sbp_rate", "0")).replace(",", "."))
    if rate > 0:
        meta = _mp_meta(mp, game_name)
        meta["sbp_rate"] = rate
        _mp_set_meta(mp, game_name, meta)
        _save_mp(ADMIN_ID, mp)
    return redirect_to(f"/minprice/game/{game_hash}")


@app.post("/minprice/game/{game_hash}/offer")
async def minprice_set_offer(request: Request, game_hash: str, item_id: str = Form(...), offer_ids: str = Form(""), user=Depends(require_session)):
    mp = _load_mp(ADMIN_ID)
    game_name = None
    for name in mp.keys():
        if _mp_hash(name) == game_hash:
            game_name = name
            break
    if not game_name or item_id not in mp.get(game_name, {}):
        return redirect_to(f"/minprice/game/{game_hash}")

    ids = []
    for part in offer_ids.replace(",", " ").split():
        part = part.strip().lstrip("#")
        if part.isdigit():
            ids.append(int(part))
    mp[game_name][item_id]["offer_ids"] = ids
    _save_mp(ADMIN_ID, mp)
    return redirect_to(f"/minprice/game/{game_hash}")


# ====================== DEMPING ======================

@app.get("/demping")
async def demping_page(request: Request, user=Depends(require_session)):
    from handlers.demping import load_demping, load_demping_settings
    demping = load_demping()
    settings = load_demping_settings()
    return templates.TemplateResponse(request=request, name="demping.html", context={
        "user": user, "demping": demping, "settings": settings, "lot_count": len(demping),
    })


@app.post("/demping/upload")
async def demping_upload(request: Request, user=Depends(require_session)):
    from handlers.demping import save_demping
    form = await request.form()
    file = form.get("file")
    if file and hasattr(file, "read"):
        try:
            content = await file.read()
            import json as _json
            data = _json.loads(content.decode("utf-8"))
            if isinstance(data, dict):
                save_demping(data)
        except Exception:
            pass
    return redirect_to("/demping")


@app.post("/demping/set-path")
async def demping_set_path(request: Request, user=Depends(require_session)):
    from handlers.demping import save_demping_settings, load_demping_settings
    form = await request.form()
    path = str(form.get("target_path", "")).strip()
    if path:
        settings = load_demping_settings()
        if not path.endswith(".json"):
            path = os.path.join(path, "price_optimizer_lots.json")
        settings["target_path"] = path
        save_demping_settings(settings)
    return redirect_to("/demping")


@app.post("/demping/set-restart")
async def demping_set_restart(request: Request, user=Depends(require_session)):
    from handlers.demping import save_demping_settings, load_demping_settings
    form = await request.form()
    cmd = str(form.get("restart_command", "")).strip()
    if cmd:
        settings = load_demping_settings()
        settings["restart_command"] = cmd
        save_demping_settings(settings)
    return redirect_to("/demping")


@app.post("/demping/send-cardinal")
async def demping_send_cardinal(request: Request, user=Depends(require_session)):
    from handlers.demping import load_demping_settings, DEMPING_FILE
    import shutil, subprocess
    settings = load_demping_settings()
    target = settings["target_path"]
    cmd = settings["restart_command"]
    target_dir = os.path.dirname(target) or "."
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(DEMPING_FILE, target)
    subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
    return redirect_to("/demping")


@app.post("/demping/update-prices")
async def demping_update_prices(request: Request, user=Depends(require_session)):
    from handlers.demping import load_demping, _do_update
    mp = _load_mp(ADMIN_ID)
    demping = load_demping()
    result = _do_update(mp, demping, ADMIN_ID, prefs_override={})
    return JSONResponse({"ok": True, "updated": result.get("updated_lots", 0)})


# ====================== CERTIFICATES ======================

@app.get("/certs")
async def certs_page(request: Request, user=Depends(require_session)):
    from handlers.certificates import load_certificates, load_cert_demping
    data = load_certificates(ADMIN_ID)
    cert_demping = load_cert_demping()
    games = []
    for game_name in sorted(data.keys()):
        items = {k: v for k, v in data.get(game_name, {}).items() if k != "_meta" and isinstance(v, dict)}
        meta = data.get(game_name, {}).get("_meta", {})
        rate = meta.get("rate", 0)
        games.append({
            "name": game_name,
            "hash": hashlib.md5(game_name.encode()).hexdigest()[:8],
            "items_count": len(items),
            "rate": rate,
        })
    return templates.TemplateResponse(request=request, name="certs.html", context={
        "user": user, "games": games, "demping_count": len(cert_demping),
    })


@app.get("/certs/game/{game_hash}")
async def certs_game_page(request: Request, game_hash: str, user=Depends(require_session)):
    from handlers.certificates import load_certificates
    data = load_certificates(ADMIN_ID)
    game_name = None
    for name in data.keys():
        if hashlib.md5(name.encode()).hexdigest()[:8] == game_hash:
            game_name = name
            break
    if not game_name:
        return redirect_to("/certs")
    items = {k: v for k, v in data.get(game_name, {}).items() if k != "_meta" and isinstance(v, dict)}
    meta = data.get(game_name, {}).get("_meta", {})
    return templates.TemplateResponse(request=request, name="certs_game.html", context={
        "user": user, "game_name": game_name, "game_hash": game_hash, "items": items, "rate": meta.get("rate", 0),
    })


@app.post("/certs/game/{game_hash}/rate")
async def certs_set_rate(request: Request, game_hash: str, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates
    form = await request.form()
    rate = _money(str(form.get("rate", "0")).replace(",", "."))
    data = load_certificates(ADMIN_ID)
    game_name = None
    for name in data.keys():
        if hashlib.md5(name.encode()).hexdigest()[:8] == game_hash:
            game_name = name
            break
    if game_name and rate > 0:
        if game_name not in data:
            data[game_name] = {}
        if "_meta" not in data[game_name]:
            data[game_name]["_meta"] = {}
        data[game_name]["_meta"]["rate"] = rate
        save_certificates(data, ADMIN_ID)
    return redirect_to(f"/certs/game/{game_hash}")


@app.post("/certs/game/{game_hash}/add")
async def certs_add_item(request: Request, game_hash: str, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates, calc_min_price as cert_calc
    form = await request.form()
    item_name = str(form.get("item_name", "")).strip()
    cost = _money(str(form.get("cost", "0")).replace(",", "."))
    offer_id = str(form.get("offer_id", "")).strip()
    data = load_certificates(ADMIN_ID)
    game_name = None
    for name in data.keys():
        if hashlib.md5(name.encode()).hexdigest()[:8] == game_hash:
            game_name = name
            break
    if not game_name or not item_name:
        return redirect_to(f"/certs/game/{game_hash}")
    item_id = hashlib.md5(f"cert_{item_name}_{secrets.token_hex(4)}".encode()).hexdigest()[:8]
    if game_name not in data:
        data[game_name] = {}
    data[game_name][item_id] = {
        "name": item_name, "cost": cost, "min_price": cert_calc(cost),
        "offer_id": int(offer_id) if offer_id.isdigit() else None,
    }
    save_certificates(data, ADMIN_ID)
    return redirect_to(f"/certs/game/{game_hash}")


@app.post("/certs/game/{game_hash}/delete")
async def certs_delete_item(request: Request, game_hash: str, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates
    form = await request.form()
    item_id = str(form.get("item_id", "")).strip()
    data = load_certificates(ADMIN_ID)
    game_name = None
    for name in data.keys():
        if hashlib.md5(name.encode()).hexdigest()[:8] == game_hash:
            game_name = name
            break
    if game_name and item_id in data.get(game_name, {}):
        del data[game_name][item_id]
        save_certificates(data, ADMIN_ID)
    return redirect_to(f"/certs/game/{game_hash}")


@app.post("/certs/send-cardinal")
async def certs_send_cardinal(request: Request, user=Depends(require_session)):
    from handlers.certificates import CERT_DEMPING_FILE
    from handlers.demping import get_cardinal_target_path, get_cardinal_restart_command
    import shutil, subprocess
    target = get_cardinal_target_path()
    cmd = get_cardinal_restart_command()
    target_dir = os.path.dirname(target) or "."
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(CERT_DEMPING_FILE, target)
    subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
    return redirect_to("/certs")


@app.post("/certs/update-demping")
async def certs_update_demping(request: Request, user=Depends(require_session)):
    from handlers.certificates import load_certificates, load_cert_demping, save_cert_demping, calc_site_price as cert_site_price
    data = load_certificates(ADMIN_ID)
    demping = load_cert_demping()
    updated = 0
    for game_name, game_data in data.items():
        meta = game_data.get("_meta", {})
        rate = meta.get("rate", 0)
        if rate <= 0:
            continue
        for _, info in {k: v for k, v in game_data.items() if k != "_meta" and isinstance(v, dict)}.items():
            offer_id = str(info.get("offer_id") or "")
            if not offer_id:
                continue
            new_price = cert_site_price(_money(info.get("cost", 0)), rate)
            if offer_id in demping:
                old_price = _money(demping[offer_id].get("min_price"))
                if old_price != new_price:
                    demping[offer_id]["min_price"] = new_price
                    demping[offer_id]["max_price"] = round(new_price + 200, 2)
                    updated += 1
            else:
                ntext = str(info.get("name") or "").lower()
                demping[offer_id] = {
                    "active": True,
                    "triggers": f"{ntext} ₽ | {ntext}+ RUB | {ntext} RUB | {ntext} руб | {ntext} рублей",
                    "min_price": new_price,
                    "max_price": round(new_price + 200, 2),
                    "min_rating": 3, "skip_no_rating": True, "price_step": 0.01,
                    "rounding": 0.01, "min_one_unit": False, "friends": [], "outbid_offline": False,
                }
                updated += 1
    save_cert_demping(demping)
    return JSONResponse({"ok": True, "updated": updated})


# ====================== TASKS ======================

@app.get("/tasks")
async def tasks_page(request: Request, period: str = "day", user=Depends(require_session)):
    gk, ua = db.get_config()
    error = ""
    cards = []
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
        "period_label": period_label if gk else "",
    })
