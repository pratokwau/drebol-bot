import secrets

from fastapi import APIRouter, Form, Request, status

from database import web_db

from .shared import _is_valid_login, redirect_to, templates

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": ""})


@router.post("/login")
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


@router.post("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("drebol_session", "")
    if session_id:
        web_db.revoke_session(session_id)
    response = redirect_to("/login")
    response.delete_cookie("drebol_session")
    return response
