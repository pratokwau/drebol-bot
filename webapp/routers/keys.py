from fastapi import APIRouter, Depends, Request

from database import db, web_db

from .shared import _login_pair, redirect_to, require_session, templates

router = APIRouter(prefix="/keys")


@router.get("")
async def keys_page(request: Request, user=Depends(require_session)):
    from handlers.ai_settings import load_ai_settings
    gk, ua = db.get_config()
    ai = load_ai_settings()
    accounts = web_db.list_accounts() if hasattr(web_db, "list_accounts") else []
    return templates.TemplateResponse(request=request, name="keys.html", context={
        "user": user, "gk": gk or "", "ua": ua or "",
        "groq_key": ai.get("GROQ_API_KEY", ""),
        "openrouter_key": ai.get("OPENROUTER_API_KEY", ""),
        "accounts": accounts, "login_username": _login_pair()[0],
    })


@router.post("/funpay")
async def keys_save_funpay(request: Request, user=Depends(require_session)):
    form = await request.form()
    db.update_config(gk=str(form.get("gk", "")).strip() or None, ua=str(form.get("ua", "")).strip() or None)
    return redirect_to("/keys")


@router.post("/groq")
async def keys_save_groq(request: Request, user=Depends(require_session)):
    from handlers.ai_settings import load_ai_settings, save_ai_settings
    form = await request.form()
    ai = load_ai_settings()
    save_ai_settings(str(form.get("groq_key", "")).strip(), ai.get("OPENROUTER_API_KEY", ""))
    return redirect_to("/keys")


@router.post("/openrouter")
async def keys_save_openrouter(request: Request, user=Depends(require_session)):
    from handlers.ai_settings import load_ai_settings, save_ai_settings
    form = await request.form()
    ai = load_ai_settings()
    save_ai_settings(ai.get("GROQ_API_KEY", ""), str(form.get("openrouter_key", "")).strip())
    return redirect_to("/keys")


@router.post("/add-account")
async def keys_add_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", "")).strip()
    if username and password and hasattr(web_db, "add_account"):
        web_db.add_account(username, password)
    return redirect_to("/keys")


@router.post("/toggle-account")
async def keys_toggle_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    if username and hasattr(web_db, "toggle_account"):
        web_db.toggle_account(username)
    return redirect_to("/keys")


@router.post("/delete-account")
async def keys_delete_account(request: Request, user=Depends(require_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    if username and hasattr(web_db, "delete_account"):
        web_db.delete_account(username)
    return redirect_to("/keys")
