import io
import os
import subprocess
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, Response

from database import web_db

from .shared import _login_pair, redirect_to, require_session, templates

router = APIRouter(prefix="/settings")

_RESTART_CMD = "sleep 1 && (pkill -9 -f 'uvicorn webapp.app:app' || true) && sleep 1 && systemctl restart drebol-bot drebol-web"


@router.get("")
async def settings_page(request: Request, user=Depends(require_session)):
    from handlers.settings import get_user_settings
    from config import ADMIN_ID
    sessions = web_db.list_sessions()
    revoked_count = sum(1 for s in sessions if s[4])
    settings = get_user_settings(ADMIN_ID)
    accounts = web_db.list_accounts() if hasattr(web_db, "list_accounts") else []
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "user": user,
            "sessions": sessions,
            "accounts": accounts,
            "current_session": user["session_id"],
            "login_username": _login_pair()[0],
            "revoked_count": revoked_count,
            "settings": settings,
        },
    )


@router.post("/update-bot")
async def settings_update_bot(request: Request, user=Depends(require_session)):
    from handlers.settings import update_setting
    from config import ADMIN_ID
    form = await request.form()
    update_setting(ADMIN_ID, "restart_notify", "1" in str(form.get("restart_notify", "")))
    update_setting(ADMIN_ID, "admin_report_notify", "1" in str(form.get("admin_report_notify", "")))
    time_val = str(form.get("admin_report_time", "23:59")).strip()
    if time_val:
        update_setting(ADMIN_ID, "admin_report_time", time_val)
    return redirect_to("/settings")


@router.post("/accounts/add")
async def settings_add_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", "")).strip()
    if username and password and hasattr(web_db, "add_account"):
        web_db.add_account(username, password)
    return redirect_to("/settings")


@router.post("/accounts/toggle")
async def settings_toggle_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    if username and hasattr(web_db, "toggle_account"):
        web_db.toggle_account(username)
    return redirect_to("/settings")


@router.post("/accounts/delete")
async def settings_delete_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    if username and hasattr(web_db, "delete_account"):
        web_db.delete_account(username)
    return redirect_to("/settings")


@router.post("/revoke")
async def revoke_session(session_id: str = Form(...), user=Depends(require_session)):
    web_db.revoke_session(session_id)
    if session_id == user["session_id"]:
        response = redirect_to("/login")
        response.delete_cookie("drebol_session")
        return response
    return redirect_to("/settings")


@router.post("/revoke-all")
async def revoke_all_sessions(user=Depends(require_session)):
    sessions = web_db.list_sessions()
    for session_id, _, _, _, revoked in sessions:
        if revoked and session_id != user["session_id"]:
            web_db.delete_session(session_id)
    return redirect_to("/settings")


@router.post("/update")
async def settings_update(user=Depends(require_session)):
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)) or ".")
        output = result.stdout.strip() + result.stderr.strip()
        subprocess.Popen(
            ["sh", "-c", _RESTART_CMD],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return JSONResponse({"ok": True, "output": output[:500]})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@router.get("/export-db")
async def settings_export_db(user=Depends(require_session)):
    from base_store import BASE_DIR

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if BASE_DIR.exists():
            for path in BASE_DIR.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=str(path.relative_to(BASE_DIR)))
    buf.seek(0)

    filename = f"drebolbot-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import-db")
async def settings_import_db(request: Request, user=Depends(require_session)):
    from base_store import BASE_DIR

    form = await request.form()
    file = form.get("file")
    if not file or not hasattr(file, "read"):
        return JSONResponse({"ok": False, "error": "Файл не выбран"}, status_code=400)

    try:
        content = await file.read()
        zbuf = io.BytesIO(content)
        with zipfile.ZipFile(zbuf) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                return JSONResponse({"ok": False, "error": "Архив пуст"}, status_code=400)

            base_resolved = BASE_DIR.resolve()
            extracted = 0
            for name in names:
                dest = (BASE_DIR / name).resolve()
                if base_resolved not in dest.parents and dest != base_resolved:
                    continue  # защита от zip-slip — путь вне data/
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(dest, "wb") as out:
                    out.write(src.read())
                extracted += 1
    except zipfile.BadZipFile:
        return JSONResponse({"ok": False, "error": "Файл повреждён или это не .zip"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)

    try:
        import base_store
        for conn in list(base_store._CONNS.values()):
            try:
                conn.close()
            except Exception:
                pass
        base_store._CONNS.clear()
    except Exception:
        pass

    subprocess.Popen(
        ["sh", "-c", _RESTART_CMD],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    return JSONResponse({"ok": True, "extracted": extracted})
