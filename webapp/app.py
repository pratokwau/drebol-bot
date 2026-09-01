import os
import json
import asyncio
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import ADMIN_ID
from database import ProfitDatabase, db, orders_db

from handlers.funpay_admin import (
    clean_price,
    fetch_funpay_sales,
    make_funpay_account,
)
from handlers.minprice import load_mp as _load_mp, save_mp as _save_mp

from webapp.routers.shared import (
    _money,
    _notif_count_safe,
    _parse_date,
    _load_notifications,
    add_notification,
    templates,
)

from webapp.routers import auth, dashboard, keys, notifications, orders, profits, settings, minprice, demping, certs, tasks


APP_ROOT = os.path.dirname(__file__)

app = FastAPI(title="Drebol-bot Web", version="3.0")
app.mount("/static", StaticFiles(directory=os.path.join(APP_ROOT, "static")), name="static")

templates.env.globals["notification_count"] = _notif_count_safe

# --- Include routers ---
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(keys.router)
app.include_router(notifications.router)
app.include_router(orders.router)
app.include_router(profits.router)
app.include_router(settings.router)
app.include_router(minprice.router)
app.include_router(demping.router)
app.include_router(certs.router)
app.include_router(tasks.router)


# --- Legacy redirect ---
@app.get("/funpay")
async def funpay_redirect():
    return RedirectResponse("/keys", status_code=303)


@app.get("/calc")
async def calc_page(request: Request):
    from webapp.routers.shared import require_session
    user = require_session(request)
    return templates.TemplateResponse(request=request, name="calc.html", context={"user": user})


# --- Exception handlers ---

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


# --- Background tasks ---

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
            user_settings = get_user_settings(ADMIN_ID)
            report_time = user_settings.get("admin_report_time", "23:59")

            existing = _load_notifications()

            if _last_sbp_check_date != today_key and current_time >= "10:00":
                _last_sbp_check_date = today_key
                try:
                    from handlers.minprice import check_sbp_rates_for_admin
                    print("[BG] Запуск автоматической проверки СБП ставок...")
                    await check_sbp_rates_for_admin()

                    mp = _load_mp(ADMIN_ID)
                    auto_updated = []
                    for gname, gdata in mp.items():
                        meta = gdata.get("_meta", {})
                        old_rate = meta.get("sbp_rate")
                        new_rate = meta.get("latest_checked_rate")
                        if old_rate and new_rate and round(float(old_rate), 6) != round(float(new_rate), 6):
                            mp[gname]["_meta"]["sbp_rate"] = new_rate
                            auto_updated.append(f"  {gname}: {old_rate} → {new_rate}")

                    try:
                        from handlers.certificates import load_certificates, save_certificates
                        certs_data = load_certificates(ADMIN_ID)
                        cert_updated = []
                        for gname, gdata in certs_data.items():
                            meta = gdata.get("_meta", {})
                            old_rate = meta.get("rate")
                            new_rate = meta.get("latest_checked_rate")
                            if old_rate and new_rate and round(float(old_rate), 6) != round(float(new_rate), 6):
                                certs_data[gname]["_meta"]["rate"] = new_rate
                                cert_updated.append(f"  🎁 {gname}: {old_rate} → {new_rate}")
                        if cert_updated:
                            save_certificates(certs_data, ADMIN_ID)
                            auto_updated.extend(cert_updated)
                    except Exception:
                        pass

                    if auto_updated:
                        _save_mp(ADMIN_ID, mp)
                        add_notification(
                            f"💱 Автообновление СБП:\n" + "\n".join(auto_updated),
                            "warning"
                        )
                        try:
                            from handlers.demping import load_demping, _do_update
                            demping_data = load_demping()
                            result = _do_update(mp, demping_data, ADMIN_ID, prefs_override={})
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
                    demping_data = load_demping()
                    result = _do_update(mp, demping_data, ADMIN_ID, prefs_override={})
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
