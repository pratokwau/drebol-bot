import json
import os
import shutil
import subprocess
import tempfile

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from config import ADMIN_ID

from handlers.minprice import (
    load_mp as _load_mp,
    get_item_offer_ids,
    calc_min_price,
)

from .shared import _money, redirect_to, require_session, templates

router = APIRouter(prefix="/demping")


@router.get("")
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


@router.post("/upload")
async def demping_upload(request: Request, user=Depends(require_session)):
    from handlers.demping import save_demping
    form = await request.form()
    file = form.get("file")
    if file and hasattr(file, "read"):
        try:
            content = await file.read()
            import re as _re
            from collections import Counter
            text = content.decode("utf-8")
            all_keys = _re.findall(r'"(\d+)"\s*:', text)
            dupes = {k: c for k, c in Counter(all_keys).items() if c > 1}
            dup_count = sum(c - 1 for c in dupes.values())
            data = json.loads(text)
            if isinstance(data, dict):
                save_demping(data)
                msg = f"📥 Демпинг загружен: {len(data)} лотов"
                if dup_count:
                    dup_ids = ", ".join(sorted(dupes.keys())[:10])
                    msg += f" (⚠️ {dup_count} дубликатов offer_id в файле: {dup_ids}{'...' if len(dupes) > 10 else ''})"
                from .shared import add_notification
                add_notification(msg, "warning" if dup_count else "success")
        except Exception:
            pass
    return redirect_to("/demping")


@router.get("/selective")
async def demping_selective_page(request: Request, user=Depends(require_session)):
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


@router.post("/selective-send")
async def demping_selective_send(request: Request, user=Depends(require_session)):
    from handlers.demping import load_demping, load_demping_settings

    form = await request.form()
    mp = _load_mp(ADMIN_ID)
    demping = load_demping()
    settings = load_demping_settings()
    target = settings["target_path"]
    cmd = settings["restart_command"]

    prefs = {}
    for key in form.keys():
        if key.startswith("cb_"):
            game_name = key[3:]
            prefs[game_name] = str(form.get(key, "no"))

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

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(demping, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    target_dir = os.path.dirname(target) or "."
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(tmp.name, target)
    os.unlink(tmp.name)
    subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
    return redirect_to("/demping")


@router.post("/set-path")
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


@router.post("/set-restart")
async def demping_set_restart(request: Request, user=Depends(require_session)):
    from handlers.demping import save_demping_settings, load_demping_settings
    form = await request.form()
    cmd = str(form.get("restart_command", "")).strip()
    if cmd:
        settings = load_demping_settings()
        settings["restart_command"] = cmd
        save_demping_settings(settings)
    return redirect_to("/demping")


@router.get("/download")
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


@router.post("/send-cardinal")
async def demping_send_cardinal(request: Request, user=Depends(require_session)):
    from handlers.demping import load_demping, load_demping_settings

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

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(demping, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    target_dir = os.path.dirname(target) or "."
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(tmp.name, target)
    os.unlink(tmp.name)
    subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
    return redirect_to("/demping")


@router.post("/update-prices")
async def demping_update_prices(request: Request, user=Depends(require_session)):
    from handlers.demping import load_demping, _do_update
    mp = _load_mp(ADMIN_ID)
    demping = load_demping()
    result = _do_update(mp, demping, ADMIN_ID, prefs_override={})
    return JSONResponse({"ok": True, "updated": result.get("updated_lots", 0)})


@router.post("/certs-upload")
async def demping_certs_upload(request: Request, user=Depends(require_session)):
    from handlers.certificates import save_cert_demping
    form = await request.form()
    file = form.get("file")
    if file and hasattr(file, "read"):
        try:
            content = await file.read()
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, dict):
                save_cert_demping(data)
                from .shared import add_notification
                add_notification(f"📥 Демпинг сертификатов загружен: {len(data)} лотов", "success")
        except Exception:
            pass
    return redirect_to("/demping")


@router.get("/certs-download")
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
