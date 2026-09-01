from fastapi import APIRouter, Depends, Request

from .shared import (
    _load_notifications,
    _mark_all_read,
    _save_notifications,
    redirect_to,
    require_session,
    templates,
)

router = APIRouter(prefix="/notifications")


@router.get("")
async def notifications_page(request: Request, user=Depends(require_session)):
    _mark_all_read()
    notifs = _load_notifications()
    return templates.TemplateResponse(request=request, name="notifications.html", context={
        "user": user, "notifications": notifs,
    })


@router.post("/clear")
async def notifications_clear(request: Request, user=Depends(require_session)):
    _save_notifications([])
    return redirect_to("/notifications")
