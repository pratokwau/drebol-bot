from fastapi import APIRouter, Depends, Request, Response

from database import db

from .shared import (
    _all_profit_stats,
    _load_admin_profits,
    _profit_stats,
    redirect_to,
    require_session,
    templates,
)

router = APIRouter()


@router.head("/")
async def dashboard_head():
    return Response(status_code=200)


@router.get("/")
async def dashboard(request: Request, user=Depends(require_session)):
    gk, ua = db.get_config()
    profits = _load_admin_profits()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "gk_set": bool(gk),
            "ua_set": bool(ua),
            "all_stats": _all_profit_stats(profits),
            "day_stats": _profit_stats(profits, "day"),
            "week_stats": _profit_stats(profits, "week"),
            "month_stats": _profit_stats(profits, "month"),
        },
    )
