import asyncio

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
        fetch_funpay_sales,
        find_funpay_sale_by_filter,
        get_auto_buy_prices,
        make_funpay_account,
    )

    cards, error = [], ""
    deep_search = False
    search_query = ""
    target = max(10, limit)

    try:
        gk, ua = db.get_config()
        if gk:
            def _fetch_listing():
                account = make_funpay_account(gk, ua)
                return fetch_funpay_sales(account, limit=target)

            fetched = await asyncio.to_thread(_fetch_listing)

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
            search_query = query
            found = [c for c in cards if query in str(c["id"]).lower() or query in str(c.get("product", "")).lower()]
            if not found:
                try:
                    gk, ua = db.get_config()
                    if gk:
                        def _fast_lookup():
                            account = make_funpay_account(gk, ua)
                            return find_funpay_sale_by_filter(account, query)

                        sale = await asyncio.to_thread(_fast_lookup)
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
                        else:
                            # Не нашли одним запросом по фильтру FunPay — дальше ищем
                            # порциями на клиенте, не блокируя сервер на весь скан истории.
                            deep_search = True
                except Exception:
                    deep_search = True
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
            "deep_search": deep_search,
            "search_query": search_query,
            "stats": _all_profit_stats(_load_admin_profits()),
            "total_loaded": len(cards),
        },
    )


@router.get("/search-chunk")
async def orders_search_chunk(
    request: Request,
    q: str = "",
    cursor: str = "",
    user=Depends(require_session),
):
    """
    Ищет заказ порциями по ~350 штук за вызов, продолжая с курсора предыдущего вызова.
    Клиент вызывает этот эндпоинт в цикле, пока не найдёт заказ или не получит exhausted=true.
    Так один глубокий поиск не блокирует сервер на минуты для всех пользователей разом.
    """
    from handlers.funpay_admin import clean_price, find_funpay_sale_chunk, get_auto_buy_prices, make_funpay_account

    query = q.strip().lower()
    query = query.replace("https://funpay.com/orders/", "").replace("http://funpay.com/orders/", "")
    query = query.strip("/").lstrip("#")
    if not query:
        return JSONResponse({"ok": False, "error": "Пустой запрос"}, status_code=400)

    gk, ua = db.get_config()
    if not gk:
        return JSONResponse({"ok": False, "error": "Golden Key не настроен"}, status_code=400)

    def _run():
        account = make_funpay_account(gk, ua)
        return find_funpay_sale_chunk(account, query, cursor=cursor or None, chunk_size=350)

    try:
        sale, checked, next_cursor = await asyncio.to_thread(_run)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=500)

    order = None
    if sale is not None:
        product_name = getattr(sale, "description", getattr(sale, "product_name", ""))
        sell_price = _money(clean_price(getattr(sale, "price", getattr(sale, "amount", 0))))
        order_id = str(getattr(sale, "id", ""))
        existing_cost = orders_db.get_prime_cost(order_id)
        cost = _money(existing_cost) if existing_cost is not None else None
        profit = round((sell_price * 0.97) - cost, 2) if cost is not None else None
        order = {
            "id": order_id,
            "game": _sale_game(sale),
            "product": product_name,
            "sell_price": sell_price,
            "date": str(getattr(sale, "date", getattr(sale, "created_at", ""))),
            "cost": cost,
            "profit": profit,
            "variants": get_auto_buy_prices(product_name, "", 0)[:4] if cost is None else [],
        }

    return JSONResponse({
        "ok": True,
        "found": order is not None,
        "order": order,
        "checked": checked,
        "next_cursor": next_cursor,
        "exhausted": next_cursor is None,
    })


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
