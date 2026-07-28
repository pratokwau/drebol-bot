import os
import secrets
import hashlib
import json
import asyncio
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

app = FastAPI(title="Drebol-bot Web", version="3.0")
app.mount("/static", StaticFiles(directory=os.path.join(APP_ROOT, "static")), name="static")
NOTIFICATIONS_FILE = "data/notifications.json"


def _notif_count_safe() -> int:
    try:
        if os.path.exists(NOTIFICATIONS_FILE):
            with open(NOTIFICATIONS_FILE, encoding="utf-8") as f:
                return sum(1 for n in json.load(f) if not n.get("read"))
    except Exception:
        pass
    return 0


templates.env.globals["notification_count"] = _notif_count_safe



def _build_daily_report() -> str:
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    profits = ProfitDatabase(ADMIN_ID).load_profits()
    day_profits = []
    for p in profits:
        dt = _parse_date(p.get("date", ""))
        if dt and start <= dt <= end:
            day_profits.append(p)
    sell_total = sum(_money(p.get("sell_price")) for p in day_profits)
    profit_total = sum(_money(p.get("profit")) for p in day_profits)
    count = len(day_profits)
    fp_count = sum(1 for p in day_profits if "fp" in str(p.get("type", "")).lower() or "funpay" in str(p.get("type", "")).lower())

    unfilled = 0
    gk, ua = db.get_config()
    if gk:
        try:
            account = make_funpay_account(gk, ua)
            sales = fetch_funpay_sales(account, limit=150)
            for sale in sales:
                oid = str(getattr(sale, "id", ""))
                if not oid:
                    continue
                st = str(getattr(sale, "status", "") or "")
                if "refund" in st.lower():
                    continue
                if orders_db.get_prime_cost(oid) is None:
                    unfilled += 1
        except Exception:
            pass

    mp = _load_mp(ADMIN_ID)
    sbp_changed = []
    sbp_total = 0
    for game_name in sorted(mp.keys()):
        meta = mp.get(game_name, {}).get("_meta", {})
        rate = meta.get("sbp_rate")
        latest = meta.get("latest_checked_rate")
        if rate:
            sbp_total += 1
            if latest and round(float(rate), 6) != round(float(latest), 6):
                sbp_changed.append(f"  {game_name}: {rate} → {latest}")

    report = (
        f"📊 Ежедневный отчёт {now.strftime('%d.%m.%Y')}\n\n"
        f"💰 Прибыль: {profit_total:.2f} ₽\n"
        f"📈 Продаж: {sell_total:.2f} ₽\n"
        f"📦 Заказов: {count} (FunPay: {fp_count})\n"
    )
    if unfilled > 0:
        report += f"⚠️ Хвостов: {unfilled}\n"
    else:
        report += "✅ Все заказы заполнены\n"
    if sbp_changed:
        report += f"\n💱 СБП: изменено {len(sbp_changed)} из {sbp_total} ставок за день:\n" + "\n".join(sbp_changed) + "\n"
    else:
        report += f"\n💱 СБП: все {sbp_total} ставок без изменений\n"
    return report


async def _check_unfilled_orders(today_key: str):
    """Проверяет хвосты и отправляет уведомление."""
    gk, ua = db.get_config()
    if not gk:
        return
    try:
        account = make_funpay_account(gk, ua)
        sales = fetch_funpay_sales(account, limit=150)
        unfilled = 0
        for sale in sales:
            oid = str(getattr(sale, "id", ""))
            if not oid:
                continue
            st = str(getattr(sale, "status", "") or "")
            if "refund" in st.lower():
                continue
            if orders_db.get_prime_cost(oid) is None:
                unfilled += 1
        if unfilled > 0:
            add_notification(f"⚠️ Незаполненных заказов: {unfilled}. Пора закрыть хвосты!", "warning")
    except Exception:
        pass


def _minutes_before(time_str: str, minutes: int) -> str:
    """Возвращает HH:MM на N минут раньше заданного времени."""
    try:
        h, m = map(int, time_str.split(":"))
        total = h * 60 + m - minutes
        if total < 0:
            total += 24 * 60
        return f"{total // 60:02d}:{total % 60:02d}"
    except Exception:
        return ""


async def _background_notifications():
    _last_sbp_check_date = None
    while True:
        try:
            await asyncio.sleep(120)
            now = datetime.now()
            today_key = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")

            from handlers.settings import get_user_settings
            settings = get_user_settings(ADMIN_ID)
            report_time = settings.get("admin_report_time", "23:59")

            existing = _load_notifications()

            # --- Автопроверка ставок СБП с FunPay (1 раз в день) ---
            if _last_sbp_check_date != today_key and current_time >= "10:00":
                _last_sbp_check_date = today_key
                try:
                    from handlers.minprice import check_sbp_rates_for_admin
                    print("[BG] Запуск автоматической проверки СБП ставок...")
                    await check_sbp_rates_for_admin()

                    # После проверки: если latest_checked_rate отличается от sbp_rate
                    # → обновляем sbp_rate автоматически
                    mp = _load_mp(ADMIN_ID)
                    auto_updated = []
                    for gname, gdata in mp.items():
                        meta = gdata.get("_meta", {})
                        old_rate = meta.get("sbp_rate")
                        new_rate = meta.get("latest_checked_rate")
                        if old_rate and new_rate and round(float(old_rate), 6) != round(float(new_rate), 6):
                            mp[gname]["_meta"]["sbp_rate"] = new_rate
                            auto_updated.append(f"  {gname}: {old_rate} → {new_rate}")

                    # То же для сертификатов
                    try:
                        from handlers.certificates import load_certificates, save_certificates
                        certs = load_certificates(ADMIN_ID)
                        cert_updated = []
                        for gname, gdata in certs.items():
                            meta = gdata.get("_meta", {})
                            old_rate = meta.get("rate")
                            new_rate = meta.get("latest_checked_rate")
                            if old_rate and new_rate and round(float(old_rate), 6) != round(float(new_rate), 6):
                                certs[gname]["_meta"]["rate"] = new_rate
                                cert_updated.append(f"  🎁 {gname}: {old_rate} → {new_rate}")
                        if cert_updated:
                            save_certificates(certs, ADMIN_ID)
                            auto_updated.extend(cert_updated)
                    except Exception:
                        pass

                    if auto_updated:
                        _save_mp(ADMIN_ID, mp)
                        add_notification(
                            f"💱 Автообновление СБП:\n" + "\n".join(auto_updated),
                            "warning"
                        )
                        # Обновляем цены демпинга
                        try:
                            from handlers.demping import load_demping, _do_update
                            demping = load_demping()
                            result = _do_update(mp, demping, ADMIN_ID, prefs_override={})
                            updated = result.get("updated_lots", 0)
                            if updated > 0:
                                details = result.get("updated_details", [])
                                msg = f"🔄 Цены демпинга обновлены ({updated} лотов). Выбери кэшбек и отправь файл в Cardinal!"
                                if details:
                                    msg += "\n\n" + "\n".join(details)
                                    if len(details) >= 30:
                                        msg += "\n  ..."
                                add_notification(msg, "warning")
                        except Exception:
                            pass
                    else:
                        print("[BG] СБП ставки не изменились")
                except Exception as e:
                    print(f"[BG] Ошибка проверки СБП: {e}")

            if current_time == report_time:
                already_report = any("ежедневный отчёт" in n.get("text", "").lower() and today_key in n.get("time", "") for n in existing)
                if not already_report:
                    report_text = _build_daily_report()
                    add_notification(report_text, "success")

            # Детекция ручных изменений sbp_rate (через веб-интерфейс)
            sbp_file = "data/sbp_last_rates.json"
            mp = _load_mp(ADMIN_ID)
            current_rates = {}
            for gname in mp.keys():
                meta = mp.get(gname, {}).get("_meta", {})
                rate = meta.get("sbp_rate")
                if rate:
                    current_rates[gname] = round(float(rate), 6)
            last_rates = {}
            if os.path.exists(sbp_file):
                try:
                    with open(sbp_file, encoding="utf-8") as f:
                        last_rates = json.load(f)
                except Exception:
                    pass
            changed = []
            for gname, rate in current_rates.items():
                old = last_rates.get(gname)
                if old is None or abs(float(old) - rate) > 0.000001:
                    changed.append(f"  {gname}: {old or '—'} → {rate}")
            if changed:
                add_notification(f"💱 Изменение СБП:\n" + "\n".join(changed), "warning")
                try:
                    from handlers.demping import load_demping, _do_update
                    demping = load_demping()
                    result = _do_update(mp, demping, ADMIN_ID, prefs_override={})
                    updated = result.get("updated_lots", 0)
                    if updated > 0:
                        details = result.get("updated_details", [])
                        msg = f"🔄 Цены демпинга обновлены ({updated} лотов). Выбери кэшбек и отправь файл в Cardinal!"
                        if details:
                            msg += "\n\n" + "\n".join(details)
                            if len(details) >= 30:
                                msg += "\n  ..."
                        add_notification(msg, "warning")
                except Exception:
                    pass
            with open(sbp_file, "w", encoding="utf-8") as f:
                json.dump(current_rates, f, ensure_ascii=False)

            # Уведомления о хвостах: за 20 и за 5 минут до отчёта, всего 2 раза
            tail_20 = _minutes_before(report_time, 20)
            tail_5 = _minutes_before(report_time, 5)
            existing_texts = [n.get("text", "") for n in existing if today_key in n.get("time", "")]
            tail_sent = sum(1 for t in existing_texts if "хвост" in t.lower())

            if current_time == tail_20 and tail_sent < 1:
                await _check_unfilled_orders(today_key)
            elif current_time == tail_5 and tail_sent < 2:
                await _check_unfilled_orders(today_key)

        except Exception:
            pass


@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(_background_notifications())


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
    if secrets.compare_digest(username, good_user) and secrets.compare_digest(password, good_pass):
        return True
    if hasattr(web_db, "check_account") and web_db.check_account(username, password):
        return True
    return False


def _notif_count() -> int:
    try:
        return len(_load_notifications())
    except Exception:
        return 0


def _ctx(user, **extra) -> dict:
    base = {"user": user, "notification_count": _notif_count()}
    base.update(extra)
    return base


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
    return redirect_to("/keys")


@app.get("/keys")
async def keys_page(request: Request, user=Depends(require_session)):
    from handlers.ai_settings import load_ai_settings, save_ai_settings
    gk, ua = db.get_config()
    ai = load_ai_settings()
    accounts = web_db.list_accounts() if hasattr(web_db, "list_accounts") else []
    return templates.TemplateResponse(request=request, name="keys.html", context={
        "user": user, "gk": gk or "", "ua": ua or "",
        "groq_key": ai.get("GROQ_API_KEY", ""),
        "openrouter_key": ai.get("OPENROUTER_API_KEY", ""),
        "accounts": accounts, "login_username": _login_pair()[0],
    })


@app.post("/keys/funpay")
async def keys_save_funpay(request: Request, user=Depends(require_session)):
    form = await request.form()
    db.update_config(gk=str(form.get("gk", "")).strip() or None, ua=str(form.get("ua", "")).strip() or None)
    return redirect_to("/keys")


@app.post("/keys/groq")
async def keys_save_groq(request: Request, user=Depends(require_session)):
    from handlers.ai_settings import load_ai_settings, save_ai_settings
    form = await request.form()
    ai = load_ai_settings()
    save_ai_settings(str(form.get("groq_key", "")).strip(), ai.get("OPENROUTER_API_KEY", ""))
    return redirect_to("/keys")


@app.post("/keys/openrouter")
async def keys_save_openrouter(request: Request, user=Depends(require_session)):
    from handlers.ai_settings import load_ai_settings, save_ai_settings
    form = await request.form()
    ai = load_ai_settings()
    save_ai_settings(ai.get("GROQ_API_KEY", ""), str(form.get("openrouter_key", "")).strip())
    return redirect_to("/keys")


@app.post("/keys/add-account")
async def keys_add_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", "")).strip()
    if username and password and hasattr(web_db, "add_account"):
        web_db.add_account(username, password)
    return redirect_to("/keys")


@app.post("/keys/toggle-account")
async def keys_toggle_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    if username and hasattr(web_db, "toggle_account"):
        web_db.toggle_account(username)
    return redirect_to("/keys")


@app.post("/keys/delete-account")
async def keys_delete_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    if username and hasattr(web_db, "delete_account"):
        web_db.delete_account(username)
    return redirect_to("/keys")


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


@app.get("/notifications")
async def notifications_page(request: Request, user=Depends(require_session)):
    _mark_all_read()
    notifs = _load_notifications()
    return templates.TemplateResponse(request=request, name="notifications.html", context={
        "user": user, "notifications": notifs,
    })


@app.post("/notifications/clear")
async def notifications_clear(request: Request, user=Depends(require_session)):
    _save_notifications([])
    return redirect_to("/notifications")


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
async def profits_page(request: Request, period: str = "day", page: int = 0, date_from: str = "", date_to: str = "", user=Depends(require_session)):
    try:
        profits = _load_admin_profits()
    except Exception:
        profits = []
    now = datetime.now()

    # Пользовательский период
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


@app.post("/orders/ai-fill")
async def orders_ai_fill(request: Request, user=Depends(require_session)):
    from handlers.minprice import _get_groq_client, _get_openrouter_client
    form = await request.form()
    prompt = str(form.get("prompt", "")).strip()
    limit = int(str(form.get("limit", "20")))

    if not prompt:
        return JSONResponse({"ok": False, "error": "Введите запрос"})

    # Получаем незакрытые заказы
    cards, error = _order_cards(limit=max(10, min(limit, 50)), sort="date", mode="unfilled")
    if error:
        return JSONResponse({"ok": False, "error": error})
    if not cards:
        return JSONResponse({"ok": False, "error": "Нет заказов без закупа"})

    # Собираем информацию о закупочных ценах из мин лотов
    mp = _load_mp(ADMIN_ID)
    buy_variants = {}
    for game_name, game_data in mp.items():
        if not isinstance(game_data, dict):
            continue
        items = {k: v for k, v in game_data.items() if k != "_meta" and isinstance(v, dict)}
        for item_id, info in items.items():
            name = info.get("name", "")
            cb = info.get("cashback", "none")
            cost = _money(info.get("cost", 0))
            if cost > 0:
                key = name.lower()
                if key not in buy_variants:
                    buy_variants[key] = []
                buy_variants[key].append({"name": name, "cost": cost, "cashback": cb})

    # Формируем контекст для ИИ
    orders_text = []
    for i, card in enumerate(cards):
        orders_text.append(f"#{i+1}. Заказ {card['id']}: {card['product']} — продажа {card['sell_price']} ₽")

    variants_text = []
    for key, variants in sorted(buy_variants.items()):
        for v in variants:
            cb_label = "с кэшбеком" if v["cashback"] == "yes" else "без кэшбека" if v["cashback"] == "no" else "нет кэшбека"
            variants_text.append(f"  {v['name']} ({cb_label}) — {v['cost']} ₽")

    ai_prompt = f"""Ты — менеджер по закупкам игровых товаров.

ЗАКАЗЫ БЕЗ ЗАКУПА:
{chr(10).join(orders_text)}

ДОСТУПНЫЕ ЗАКУПОЧНЫЕ ЦЕНЫ (из базы):
{chr(10).join(variants_text[:100]) if variants_text else 'Нет данных'}

ЗАПРОС МЕНЕДЖЕРА:
{prompt}

ЗАДАЧА: Для каждого заказа выбери подходящую закупочную цену из списка выше.

ПРАВИЛА:
1. Выбирай закупку по названию товара — она должна соответствовать заказу.
2. Если в запросе указано "с кэшбеком" — выбирай вариант с кэшбеком, если он есть.
3. Если с кэшбеком нет — используй без кэшбека.
4. Цена продажи в заказе может помочь определить правильный вариант (ближайшая по размеру).

ФОРМАТ ОТВЕТА (строго):
order_id → цена_закупа

Пример:
YPALBKW2 → 250.00
DEF456 → 180.50

Если для заказа не удалось подобрать:
order_id → не найдено

Отвечай ТОЛЬКО строками с →."""

    # Вызываем ИИ
    providers = [
        ("groq", "llama-3.3-70b-versatile"),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
        ("openrouter", "qwen/qwen3-next-80b-a3b-instruct:free"),
        ("groq", "llama-3.1-8b-instant"),
    ]

    ai_response = None
    for provider, model_name in providers:
        try:
            if provider == "groq":
                resp = _get_groq_client().chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": ai_prompt}],
                    max_tokens=4000,
                    temperature=0.1,
                    timeout=60
                )
            elif provider == "openrouter":
                if not _get_openrouter_client():
                    continue
                resp = _get_openrouter_client().chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": ai_prompt}],
                    max_tokens=4000,
                    temperature=0.1,
                    timeout=60
                )
            else:
                continue
            content = resp.choices[0].message.content if resp.choices else None
            if content:
                ai_response = content
                break
        except Exception:
            continue

    if not ai_response:
        return JSONResponse({"ok": False, "error": "ИИ не ответил. Попробуйте позже."})

    # Парсим ответ ИИ
    results = []
    saved_count = 0
    for line in ai_response.split("\n"):
        line = line.strip()
        if "→" not in line:
            continue
        parts = line.split("→")
        if len(parts) != 2:
            continue
        order_id = parts[0].strip().lstrip("#").strip()
        buy_str = parts[1].strip()
        if buy_str.lower() == "не найдено":
            results.append({"order_id": order_id, "buy_price": None, "status": "not_found"})
            continue
        try:
            buy_price = float(buy_str.replace(",", ".").replace("₽", "").strip())
        except ValueError:
            results.append({"order_id": order_id, "buy_price": None, "status": "parse_error"})
            continue

        # Ищем sell_price для этого заказа
        sell_price = 0
        for card in cards:
            if str(card["id"]) == order_id:
                sell_price = card["sell_price"]
                break

        orders_db.set_prime_cost(order_id, buy_price)
        saved_count += 1
        results.append({
            "order_id": order_id,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit": round((sell_price * 0.97) - buy_price, 2),
            "status": "saved"
        })

    return JSONResponse({
        "ok": True,
        "ai_response": ai_response,
        "results": results,
        "saved": saved_count,
        "total": len(cards),
    })


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
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, cwd=os.path.dirname(__file__) or ".")
        output = result.stdout.strip() + result.stderr.strip()
        subprocess.Popen(
            ["sh", "-c", "sleep 1 && pkill -9 -f uvicorn && sleep 2 && systemctl restart drebol-bot"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
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


@app.post("/minprice/rename-game")
async def minprice_rename_game(request: Request, user=Depends(require_session)):
    form = await request.form()
    old_name = str(form.get("old_name", "")).strip()
    new_name = str(form.get("new_name", "")).strip()
    if old_name and new_name and old_name != new_name:
        mp = _load_mp(ADMIN_ID)
        if old_name in mp:
            mp[new_name] = mp.pop(old_name)
            _save_mp(ADMIN_ID, mp)
    return redirect_to("/minprice")


@app.get("/minprice/import")
async def minprice_import_page(request: Request, user=Depends(require_session)):
    import re
    from FunPayAPI import Account
    gk, ua = db.get_config()
    error = ""
    profile_games = []
    if gk:
        try:
            import requests as _req
            acc = Account(gk)
            if ua:
                acc.user_agent = ua
            acc.get()
            session = _req.Session()
            session.cookies.set("golden_key", gk, domain=".funpay.com")
            session.headers["User-Agent"] = ua or "Mozilla/5.0"
            r = session.get(f"https://funpay.com/users/{acc.id}/", timeout=10)
            pairs = re.findall(r'<h3><a href="https://funpay\.com/lots/(\d+)/">([^<]+)</a></h3>', r.text)
            seen = set()
            for node_id, title in pairs:
                t = title.strip()
                if t.lower() not in seen:
                    seen.add(t.lower())
                    profile_games.append({"node_id": int(node_id), "name": t})
        except Exception as e:
            error = f"Ошибка: {e}"
    else:
        error = "Golden Key не настроен"

    mp = _load_mp(ADMIN_ID)
    existing = set(mp.keys())
    return templates.TemplateResponse(request=request, name="minprice_import.html", context={
        "user": user, "games": profile_games, "existing": existing, "error": error,
    })


@app.post("/minprice/import")
async def minprice_import_do(request: Request, user=Depends(require_session)):
    form = await request.form()
    selected = form.getlist("games")
    mp = _load_mp(ADMIN_ID)
    added = 0
    for name in selected:
        name = name.strip()
        if name and name not in mp:
            mp[name] = {}
            added += 1
    if added:
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


@app.post("/minprice/game/{game_hash}/add-variant")
async def minprice_add_variant(request: Request, game_hash: str, user=Depends(require_session)):
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
    cashback = str(form.get("cashback", "")).strip()
    cost = _money(str(form.get("cost", "0")).replace(",", "."))

    if not item_name or cashback not in ("yes", "no") or cost <= 0:
        return redirect_to(f"/minprice/game/{game_hash}")

    uid = hashlib.md5(f"{item_name}_{cashback}_{secrets.token_hex(4)}".encode()).hexdigest()[:8]
    mp[game_name][uid] = {
        "name": item_name,
        "cost": cost,
        "min_price": _calc_min_price(cost),
        "cashback": cashback,
    }
    _save_mp(ADMIN_ID, mp)
    return redirect_to(f"/minprice/game/{game_hash}")


@app.post("/minprice/game/{game_hash}/update-sbp")
async def minprice_update_sbp(request: Request, game_hash: str, user=Depends(require_session)):
    from handlers.minprice import resolve_sbp_rate_for_game
    mp = _load_mp(ADMIN_ID)
    game_name = None
    for name in mp.keys():
        if _mp_hash(name) == game_hash:
            game_name = name
            break
    if not game_name:
        return JSONResponse({"ok": False, "text": "Игра не найдена"})

    meta = _mp_meta(mp, game_name)
    gk, ua = db.get_config()
    if not gk:
        return JSONResponse({"ok": False, "text": "Golden Key не настроен"})

    lot_id = meta.get("lot_id")
    lot_id, new_rate = await resolve_sbp_rate_for_game(gk, game_name, lot_id, attempts=5)

    if new_rate is None:
        return JSONResponse({"ok": False, "text": "Не удалось получить ставку с FunPay"})

    old_rate = meta.get("sbp_rate")
    meta["lot_id"] = lot_id
    meta["latest_checked_rate"] = new_rate

    if old_rate and round(float(old_rate), 6) == round(float(new_rate), 6):
        return JSONResponse({"ok": True, "changed": False, "text": f"Ставка не изменилась: {new_rate}"})

    meta["sbp_rate"] = new_rate
    _mp_set_meta(mp, game_name, meta)
    _save_mp(ADMIN_ID, mp)

    # Обновляем демпинг
    demping_updated = 0
    try:
        from handlers.demping import load_demping, _do_update
        demping = load_demping()
        result = _do_update(mp, demping, ADMIN_ID, prefs_override={})
        demping_updated = result.get("updated_lots", 0)
    except Exception:
        pass

    old_text = f"{old_rate}" if old_rate else "—"
    text = f"СБП обновлён: {old_text} → {new_rate}"
    if demping_updated:
        text += f" | Демпинг: {demping_updated} лотов"
    return JSONResponse({"ok": True, "changed": True, "text": text})


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


@app.post("/minprice/game/{game_hash}/autolink")
async def minprice_autolink(request: Request, game_hash: str, user=Depends(require_session)):
    import traceback as _tb
    from handlers.minprice import _get_user_lots, _match_offers_with_ai, CASHBACK_OPTIONS
    try:
        mp = _load_mp(ADMIN_ID)
        game_name = None
        for name in mp.keys():
            if _mp_hash(name) == game_hash:
                game_name = name
                break
        if not game_name:
            return JSONResponse({"ok": False, "error": "Игра не найдена"})

        form = await request.form()
        mode = str(form.get("mode", "all"))

        print(f"[AUTOLINK] === ЗАПУСК === Игра: '{game_name}', режим: {mode}")

        try:
            lots = await _get_user_lots(game_name)
        except Exception as e:
            _tb.print_exc()
            return JSONResponse({"ok": False, "error": f"Ошибка скрейпинга лотов: {type(e).__name__}: {str(e)[:200]}"})

        print(f"[AUTOLINK] Лотов найдено: {len(lots) if lots else 0}")

        if not lots:
            return JSONResponse({"ok": False, "error": "Лоты не найдены на FunPay. Проверьте что профиль публичный и в настройках верный FUNPAY_USER_ID."})

        items = _mp_items(mp, game_name)
        if mode == "unlinked":
            items = {iid: info for iid, info in items.items() if isinstance(info, dict) and not _mp_offer_ids(info)}

        if not items:
            return JSONResponse({"ok": False, "error": "Нет товаров для сопоставления"})

        print(f"[AUTOLINK] Товаров: {len(items)}")

        try:
            matches = await _match_offers_with_ai(game_name, lots, items)
        except Exception as e:
            _tb.print_exc()
            return JSONResponse({"ok": False, "error": f"Ошибка ИИ: {type(e).__name__}: {str(e)[:200]}"})

        print(f"[AUTOLINK] ИИ совпадений: {len(matches)}")

        saved = 0
        for full_name, offer_ids in matches.items():
            for item_id, info in mp[game_name].items():
                if item_id == "_meta" or not isinstance(info, dict):
                    continue
                name = info.get("name", "")
                cashback = info.get("cashback", "none")
                cb_label = CASHBACK_OPTIONS.get(cashback, "")
                item_full = f"{name} ({cb_label})" if cb_label else name
                if item_full == full_name:
                    mp[game_name][item_id]["offer_ids"] = offer_ids
                    saved += 1
                    break

        _save_mp(ADMIN_ID, mp)
        print(f"[AUTOLINK] Сохранено: {saved}")
        return JSONResponse({"ok": True, "saved": saved, "total": len(matches), "lots_found": len(lots)})
    except Exception as e:
        _tb.print_exc()
        return JSONResponse({"ok": False, "error": f"Критическая ошибка: {type(e).__name__}: {str(e)[:300]}"})


# ====================== DEMPING ======================

@app.get("/demping")
async def demping_page(request: Request, user=Depends(require_session)):
    from handlers.demping import load_demping, load_demping_settings
    from handlers.certificates import load_cert_demping
    demping = load_demping()
    cert_demping = load_cert_demping()
    settings = load_demping_settings()
    return templates.TemplateResponse(request=request, name="demping.html", context={
        "user": user, "demping": demping, "settings": settings,
        "mp_lot_count": len(demping),
        "cert_lot_count": len(cert_demping),
        "cert_target": settings["target_path"],
        "cert_restart": settings["restart_command"],
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
            text = content.decode("utf-8")

            # Считаем дубликаты offer_id для уведомления
            import re as _re
            from collections import Counter
            all_keys = _re.findall(r'"(\d+)"\s*:', text)
            dupes = {k: c for k, c in Counter(all_keys).items() if c > 1}
            dup_count = sum(c - 1 for c in dupes.values())

            # Стандартный парсинг — при дубликатах offer_id последнее значение побеждает
            data = _json.loads(text)
            if isinstance(data, dict):
                save_demping(data)
                msg = f"📥 Демпинг загружен: {len(data)} лотов"
                if dup_count:
                    dup_ids = ", ".join(sorted(dupes.keys())[:10])
                    msg += f" (⚠️ {dup_count} дубликатов offer_id в файле: {dup_ids}{'...' if len(dupes) > 10 else ''})"
                from handlers.demping import add_notification as _add_notif
                _add_notif(msg, "warning" if dup_count else "success")
        except Exception:
            pass
    return redirect_to("/demping")


@app.get("/demping/selective")
async def demping_selective_page(request: Request, user=Depends(require_session)):
    from handlers.minprice import get_item_offer_ids
    mp = _load_mp(ADMIN_ID)
    games = []
    for game_name in sorted(mp.keys()):
        game_data = mp.get(game_name, {})
        meta = game_data.get("_meta", {})
        sbp_rate = meta.get("sbp_rate")
        if not sbp_rate:
            continue
        items = {k: v for k, v in game_data.items() if k != "_meta" and isinstance(v, dict)}
        linked_items = []
        has_yes = False
        has_no = False
        for item_id, info in items.items():
            ids = get_item_offer_ids(info)
            if not ids:
                continue
            cb = info.get("cashback", "none")
            if cb == "yes":
                has_yes = True
            elif cb == "no":
                has_no = True
            linked_items.append({"name": info.get("name", ""), "cashback": cb, "offer_ids": ids})
        if linked_items:
            games.append({
                "name": game_name,
                "has_yes": has_yes,
                "has_no": has_no,
                "default": "yes" if has_yes and not has_no else "no",
                "items_count": len(linked_items),
            })
    return templates.TemplateResponse(request=request, name="demping_selective.html", context={
        "user": user, "games": games,
    })


@app.post("/demping/selective-send")
async def demping_selective_send(request: Request, user=Depends(require_session)):
    from handlers.minprice import get_item_offer_ids, calc_min_price
    from handlers.demping import load_demping, load_demping_settings
    import shutil, subprocess

    form = await request.form()
    mp = _load_mp(ADMIN_ID)
    demping = load_demping()
    settings = load_demping_settings()
    target = settings["target_path"]
    cmd = settings["restart_command"]

    # Собираем prefs из формы: game_name -> cashback choice
    prefs = {}
    for key in form.keys():
        if key.startswith("cb_"):
            game_name = key[3:]
            prefs[game_name] = str(form.get(key, "no"))

    # Пересчитываем цены по выбранным кэшбекам
    offer_to_site_price = {}
    for game_name, game_data in mp.items():
        if not isinstance(game_data, dict):
            continue
        meta = game_data.get("_meta", {})
        sbp_rate = _money(meta.get("sbp_rate", 0))
        if not sbp_rate:
            continue
        pref = prefs.get(game_name, "no")
        items = {k: v for k, v in game_data.items() if k != "_meta" and isinstance(v, dict)}
        for item_id, info in items.items():
            ids = get_item_offer_ids(info)
            if not ids:
                continue
            cost = _money(info.get("cost", 0))
            if cost <= 0:
                continue
            min_price = calc_min_price(cost)
            site_price = round(min_price * sbp_rate, 2)
            cb = info.get("cashback", "none")
            for oid in ids:
                if oid not in offer_to_site_price:
                    offer_to_site_price[oid] = site_price
                if cb == pref:
                    offer_to_site_price[oid] = site_price

    for oid_str, lot in demping.items():
        oid = int(oid_str)
        if oid in offer_to_site_price:
            lot["min_price"] = offer_to_site_price[oid]

    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(demping, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    target_dir = os.path.dirname(target) or "."
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(tmp.name, target)
    os.unlink(tmp.name)
    subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
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


@app.get("/demping/download")
async def demping_download(user=Depends(require_session)):
    from handlers.demping import DEMPING_FILE
    if not os.path.exists(DEMPING_FILE):
        return JSONResponse({"ok": False, "error": "Файл не найден"}, status_code=404)
    with open(DEMPING_FILE, "rb") as f:
        data = f.read()
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=price_optimizer_lots.json"},
    )


@app.post("/demping/send-cardinal")
async def demping_send_cardinal(request: Request, user=Depends(require_session)):
    from handlers.demping import load_demping, load_demping_settings, DEMPING_FILE
    from handlers.minprice import get_item_offer_ids, calc_min_price
    import shutil, subprocess

    form = await request.form()
    cashback = str(form.get("cashback", "all"))

    settings = load_demping_settings()
    target = settings["target_path"]
    cmd = settings["restart_command"]

    mp = _load_mp(ADMIN_ID)
    demping = load_demping()

    offer_to_site_price = {}
    for game_name, game_data in mp.items():
        if not isinstance(game_data, dict):
            continue
        meta = game_data.get("_meta", {})
        sbp_rate = _money(meta.get("sbp_rate", 0))
        for item_id, info in game_data.items():
            if item_id == "_meta" or not isinstance(info, dict):
                continue
            ids = get_item_offer_ids(info)
            if not ids:
                continue
            cost = _money(info.get("cost", 0))
            if cost <= 0:
                continue
            min_price = calc_min_price(cost)
            site_price = round(min_price * sbp_rate, 2) if sbp_rate > 0 else min_price
            cb = info.get("cashback", "none")
            for oid in ids:
                if cashback == "all":
                    offer_to_site_price[oid] = site_price
                elif cb == cashback:
                    offer_to_site_price[oid] = site_price
                elif oid not in offer_to_site_price:
                    offer_to_site_price[oid] = site_price

    for oid_str, lot in demping.items():
        oid = int(oid_str)
        if oid in offer_to_site_price:
            lot["min_price"] = offer_to_site_price[oid]

    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(demping, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    target_dir = os.path.dirname(target) or "."
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(tmp.name, target)
    os.unlink(tmp.name)

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
        linked = sum(1 for v in items.values() if v.get("offer_id"))
        games.append({
            "name": game_name,
            "hash": hashlib.md5(game_name.encode()).hexdigest()[:8],
            "items_count": len(items),
            "linked_count": linked,
            "rate": rate,
        })
    return templates.TemplateResponse(request=request, name="certs.html", context={
        "user": user, "games": games, "demping_count": len(cert_demping),
    })


@app.post("/certs/add-game")
async def certs_add_game(request: Request, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates
    form = await request.form()
    game_name = str(form.get("game_name", "")).strip()
    if game_name:
        data = load_certificates(ADMIN_ID)
        if game_name not in data:
            data[game_name] = {}
            save_certificates(data, ADMIN_ID)
    return redirect_to("/certs")


@app.post("/certs/delete-game")
async def certs_delete_game(request: Request, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates
    form = await request.form()
    game_name = str(form.get("game_name", "")).strip()
    if game_name:
        data = load_certificates(ADMIN_ID)
        if game_name in data:
            del data[game_name]
            save_certificates(data, ADMIN_ID)
    return redirect_to("/certs")


@app.post("/certs/rename-game")
async def certs_rename_game(request: Request, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates
    form = await request.form()
    old_name = str(form.get("old_name", "")).strip()
    new_name = str(form.get("new_name", "")).strip()
    if old_name and new_name and old_name != new_name:
        data = load_certificates(ADMIN_ID)
        if old_name in data:
            data[new_name] = data.pop(old_name)
            save_certificates(data, ADMIN_ID)
    return redirect_to("/certs")


@app.get("/certs/import")
async def certs_import_page(request: Request, user=Depends(require_session)):
    from handlers.certificates import load_certificates
    import re
    gk, ua = db.get_config()
    error = ""
    profile_games = []
    if gk:
        try:
            from FunPayAPI import Account
            import requests as _req
            acc = Account(gk)
            if ua:
                acc.user_agent = ua
            acc.get()
            session = _req.Session()
            session.cookies.set("golden_key", gk, domain=".funpay.com")
            session.headers["User-Agent"] = ua or "Mozilla/5.0"
            r = session.get(f"https://funpay.com/users/{acc.id}/", timeout=10)
            pairs = re.findall(r'<h3><a href="https://funpay\.com/lots/(\d+)/">([^<]+)</a></h3>', r.text)
            seen = set()
            for node_id, title in pairs:
                t = title.strip()
                if t.lower() not in seen:
                    seen.add(t.lower())
                    profile_games.append({"node_id": int(node_id), "name": t})
        except Exception as e:
            error = f"Ошибка: {e}"
    else:
        error = "Golden Key не настроен"

    data = load_certificates(ADMIN_ID)
    existing = set(data.keys())
    return templates.TemplateResponse(request=request, name="certs_import.html", context={
        "user": user, "games": profile_games, "existing": existing, "error": error,
    })


@app.post("/certs/import")
async def certs_import_do(request: Request, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates
    form = await request.form()
    selected = form.getlist("games")
    data = load_certificates(ADMIN_ID)
    added = 0
    for name in selected:
        name = name.strip()
        if name and name not in data:
            data[name] = {}
            added += 1
    if added:
        save_certificates(data, ADMIN_ID)
    return redirect_to("/certs")


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


@app.post("/certs/game/{game_hash}/update-rate")
async def certs_update_rate(request: Request, game_hash: str, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates
    from handlers.minprice import resolve_sbp_rate_for_game
    data = load_certificates(ADMIN_ID)
    game_name = None
    for name in data.keys():
        if hashlib.md5(name.encode()).hexdigest()[:8] == game_hash:
            game_name = name
            break
    if not game_name:
        return JSONResponse({"ok": False, "text": "Игра не найдена"})

    meta = data.get(game_name, {}).get("_meta", {})
    gk, ua = db.get_config()
    if not gk:
        return JSONResponse({"ok": False, "text": "Golden Key не настроен"})

    lot_id = meta.get("lot_id")
    lot_id, new_rate = await resolve_sbp_rate_for_game(gk, game_name, lot_id, attempts=5)

    if new_rate is None:
        return JSONResponse({"ok": False, "text": "Не удалось получить ставку с FunPay"})

    old_rate = meta.get("rate")
    meta["lot_id"] = lot_id
    meta["latest_checked_rate"] = new_rate

    if old_rate and round(float(old_rate), 6) == round(float(new_rate), 6):
        return JSONResponse({"ok": True, "changed": False, "text": f"Ставка не изменилась: {new_rate}"})

    meta["rate"] = new_rate
    data[game_name]["_meta"] = meta
    save_certificates(data, ADMIN_ID)
    old_text = f"{old_rate}" if old_rate else "—"
    return JSONResponse({"ok": True, "changed": True, "text": f"Коэффициент обновлён: {old_text} → {new_rate}"})


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


@app.post("/certs/game/{game_hash}/edit/{item_id}")
async def certs_edit_item(request: Request, game_hash: str, item_id: str, user=Depends(require_session)):
    from handlers.certificates import load_certificates, save_certificates, calc_min_price as cert_calc
    form = await request.form()
    data = load_certificates(ADMIN_ID)
    game_name = None
    for name in data.keys():
        if hashlib.md5(name.encode()).hexdigest()[:8] == game_hash:
            game_name = name
            break
    if not game_name or item_id not in data.get(game_name, {}):
        return redirect_to(f"/certs/game/{game_hash}")

    new_name = str(form.get("item_name", "")).strip()
    new_cost = _money(str(form.get("cost", "0")).replace(",", "."))
    new_offer = str(form.get("offer_id", "")).strip()

    if new_name:
        data[game_name][item_id]["name"] = new_name
    if new_cost > 0:
        data[game_name][item_id]["cost"] = new_cost
        data[game_name][item_id]["min_price"] = cert_calc(new_cost)
    if new_offer:
        data[game_name][item_id]["offer_id"] = int(new_offer) if new_offer.isdigit() else None

    save_certificates(data, ADMIN_ID)
    return redirect_to(f"/certs/game/{game_hash}")


@app.post("/demping/certs-upload")
async def demping_certs_upload(request: Request, user=Depends(require_session)):
    from handlers.certificates import CERT_DEMPING_FILE, save_cert_demping
    form = await request.form()
    file = form.get("file")
    if file and hasattr(file, "read"):
        try:
            content = await file.read()
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, dict):
                save_cert_demping(data)
                from handlers.demping import add_notification as _add_notif
                _add_notif(f"📥 Демпинг сертификатов загружен: {len(data)} лотов", "success")
        except Exception:
            pass
    return redirect_to("/demping")


@app.get("/demping/certs-download")
async def demping_certs_download(user=Depends(require_session)):
    from handlers.certificates import CERT_DEMPING_FILE
    if not os.path.exists(CERT_DEMPING_FILE):
        return JSONResponse({"ok": False, "error": "Файл не найден"}, status_code=404)
    with open(CERT_DEMPING_FILE, "rb") as f:
        data = f.read()
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=certificates_demping.json"},
    )


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
