import hashlib
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from config import ADMIN_ID
from database import db

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

from .shared import _money, redirect_to, require_session, templates

router = APIRouter(prefix="/minprice")


@router.get("")
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


@router.post("/add-game")
async def minprice_add_game(request: Request, user=Depends(require_session)):
    form = await request.form()
    game_name = str(form.get("game_name", "")).strip()
    if game_name:
        mp = _load_mp(ADMIN_ID)
        if game_name not in mp:
            mp[game_name] = {}
        _save_mp(ADMIN_ID, mp)
    return redirect_to("/minprice")


@router.post("/delete-game")
async def minprice_delete_game(request: Request, user=Depends(require_session)):
    form = await request.form()
    game_name = str(form.get("game_name", "")).strip()
    if game_name:
        mp = _load_mp(ADMIN_ID)
        if game_name in mp:
            del mp[game_name]
            _save_mp(ADMIN_ID, mp)
    return redirect_to("/minprice")


@router.post("/rename-game")
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


@router.get("/import")
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


@router.post("/import")
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


@router.get("/game/{game_hash}")
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


@router.post("/game/{game_hash}/add")
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


@router.post("/game/{game_hash}/add-variant")
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


@router.post("/game/{game_hash}/update-sbp")
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


@router.post("/game/{game_hash}/edit/{item_id}")
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


@router.post("/game/{game_hash}/delete")
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


@router.post("/game/{game_hash}/sbp")
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


@router.post("/game/{game_hash}/offer")
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


@router.post("/game/{game_hash}/autolink")
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
