from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from database import db, orders_db, web_db

from .shared import (
    _all_profit_stats,
    _load_admin_profits,
    _money,
    _order_payload,
    _sale_game,
    redirect_to,
    require_session,
    templates,
)

router = APIRouter(prefix="/orders")


@router.get("")
async def orders_page(
    request: Request,
    sort: str = "date",
    mode: str = "all",
    limit: int = 120,
    q: str = "",
    user=Depends(require_session),
):
    from handlers.funpay_admin import (
        clean_price,
        extract_order_amount,
        fetch_funpay_sales_window,
        find_funpay_sale,
        get_auto_buy_prices,
        make_funpay_account,
    )

    cards, error = [], ""
    target = max(10, limit)

    try:
        gk, ua = db.get_config()
        if gk:
            account = make_funpay_account(gk, ua)
            fetched = []
            offset = 0
            while len(fetched) < target:
                batch = fetch_funpay_sales_window(account, offset=offset, limit=500)
                if not batch:
                    break
                fetched.extend(batch)
                offset += 500
                if len(batch) < 500:
                    break
            fetched = fetched[:target]

            for sale in fetched:
                order_id = str(getattr(sale, "id", ""))
                if not order_id:
                    continue
                status_text = str(getattr(sale, "status", "") or "")
                if "refund" in status_text.lower():
                    continue
                sell_price = _money(clean_price(getattr(sale, "price", getattr(sale, "amount", 0))))
                product_name = getattr(sale, "description", getattr(sale, "product_name", "Без названия"))
                order_date = str(getattr(sale, "date", getattr(sale, "created_at", "")))
                order_amount = extract_order_amount(product_name)
                order_game = _sale_game(sale)
                cost = orders_db.get_prime_cost(order_id)
                if mode == "unfilled" and cost is not None:
                    continue
                if mode == "filled" and cost is None:
                    continue
                sell_override = orders_db.get_sell_price(order_id) if hasattr(orders_db, "get_sell_price") else None
                if sell_override is not None:
                    sell_price = _money(sell_override)
                profit = (sell_price * 0.97) - _money(cost) if cost is not None else None
                variants = get_auto_buy_prices(product_name, order_game, order_amount)[:4] if cost is None else []
                cards.append({
                    "id": order_id,
                    "game": order_game,
                    "product": product_name,
                    "sell_price": sell_price,
                    "date": order_date,
                    "cost": _money(cost) if cost is not None else None,
                    "profit": round(profit, 2) if profit is not None else None,
                    "variants": variants,
                })
        if q.strip():
            query = q.strip().lower()
            query = query.replace("https://funpay.com/orders/", "").replace("http://funpay.com/orders/", "")
            query = query.strip("/").lstrip("#").lower()
            found = [c for c in cards if query in str(c["id"]).lower() or query in str(c.get("product", "")).lower()]
            if not found:
                try:
                    gk, ua = db.get_config()
                    if gk:
                        account = make_funpay_account(gk, ua)
                        sale, _ = find_funpay_sale(account, query, max_depth=5000)
                        if sale:
                            product_name = getattr(sale, "description", getattr(sale, "product_name", ""))
                            sell_price = _money(clean_price(getattr(sale, "price", getattr(sale, "amount", 0))))
                            order_id = str(getattr(sale, "id", ""))
                            existing_cost = orders_db.get_prime_cost(order_id)
                            cost = _money(existing_cost) if existing_cost is not None else None
                            profit = round((sell_price * 0.97) - cost, 2) if cost is not None else None
                            found.append({
                                "id": order_id,
                                "game": _sale_game(sale),
                                "product": product_name,
                                "sell_price": sell_price,
                                "date": str(getattr(sale, "date", getattr(sale, "created_at", ""))),
                                "cost": cost,
                                "profit": profit,
                                "variants": get_auto_buy_prices(product_name, "", 0)[:4] if cost is None else [],
                            })
                except Exception:
                    pass
            cards = found
    except Exception as exc:
        error = f"Ошибка загрузки заказов: {exc}"
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
            "total_loaded": len(cards),
        },
    )


@router.post("/save-cost")
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


@router.post("/save-price")
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
