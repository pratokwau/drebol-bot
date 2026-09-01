import hashlib
import json
import os
import secrets
import shutil
import subprocess

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from config import ADMIN_ID
from database import db

from .shared import _money, redirect_to, require_session, templates

router = APIRouter(prefix="/certs")


@router.get("")
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


@router.post("/add-game")
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


@router.post("/delete-game")
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


@router.post("/rename-game")
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


@router.get("/import")
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


@router.post("/import")
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


@router.get("/game/{game_hash}")
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


@router.post("/game/{game_hash}/rate")
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


@router.post("/game/{game_hash}/update-rate")
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


@router.post("/game/{game_hash}/add")
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


@router.post("/game/{game_hash}/delete")
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


@router.post("/game/{game_hash}/edit/{item_id}")
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


@router.post("/send-cardinal")
async def certs_send_cardinal(request: Request, user=Depends(require_session)):
    from handlers.certificates import CERT_DEMPING_FILE
    from handlers.demping import get_cardinal_target_path, get_cardinal_restart_command
    target = get_cardinal_target_path()
    cmd = get_cardinal_restart_command()
    target_dir = os.path.dirname(target) or "."
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(CERT_DEMPING_FILE, target)
    subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
    return redirect_to("/certs")


@router.post("/update-demping")
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
