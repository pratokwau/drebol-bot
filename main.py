import asyncio
import sys
import json
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

sys.stdout.reconfigure(encoding='utf-8')

from aiogram import types, F
from aiogram.types import BotCommand
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from loader import bot, dp
from config import ADMIN_ID

MUTE_FILE = "data/mute_restart.json"


def _load_mutes() -> dict:
    if not os.path.exists(MUTE_FILE):
        return {}
    try:
        with open(MUTE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_mutes(mutes: dict):
    os.makedirs("data", exist_ok=True)
    with open(MUTE_FILE, "w", encoding="utf-8") as f:
        json.dump(mutes, f)


def _is_muted(uid: int) -> bool:
    exp = _load_mutes().get(str(uid))
    if not exp:
        return False
    return datetime.fromisoformat(exp) > datetime.now()


def _set_mute(uid: int, minutes: int):
    mutes = _load_mutes()
    mutes[str(uid)] = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    _save_mutes(mutes)


def _restart_mute_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔕 30 мин", callback_data="restart_mute_30"),
        InlineKeyboardButton(text="🔕 1 час",  callback_data="restart_mute_60"),
        InlineKeyboardButton(text="🔕 1 день", callback_data="restart_mute_1440"),
    ]])


from handlers.start import router as start_router
from handlers.admin import router as admin_router
from handlers.rassstart import router as rass_router
from handlers.playerokrass import router as playerok_router
from handlers.saveprofit import router as saveprofit_router
from handlers.wallet import send_daily_report, router as wallet_router
from handlers.funpay_admin import router as fp_admin_router
from handlers.tasks import remind_unfilled_orders, router as tasks_router
from handlers.minprice import router as minprice_router, check_sbp_rates_for_admin
from handlers.ai_chat import router as ai_router
from handlers.ai_settings import router as ai_settings_router
from update_manager import get_update_status, load_restart_notice, clear_restart_notice
from handlers.settings import router as settings_router
from handlers.demping import router as demping_router
from handlers.certificates import router as certificates_router
from middlewares.command_restriction import CommandRestrictionMiddleware


_last_admin_report_date = None


async def send_admin_daily_report():
    """Админский отчёт: сначала обновляет СБП, потом отправляет статистику."""
    try:
        await check_sbp_rates_for_admin()
    except Exception as e:
        print(f"[ADMIN DAILY REPORT] Ошибка проверки СБП: {e}")
    await send_daily_report(bot, ADMIN_ID)


async def check_admin_daily_report_time():
    """Каждую минуту проверяет, пора ли отправить главный админский отчёт."""
    global _last_admin_report_date
    from handlers.settings import get_user_settings

    now = datetime.now()
    settings = get_user_settings(ADMIN_ID)
    if not settings.get("admin_report_notify", True):
        return
    if settings.get("admin_report_time", "23:59") != now.strftime("%H:%M"):
        return

    today_key = now.strftime("%Y-%m-%d")
    if _last_admin_report_date == today_key:
        return

    _last_admin_report_date = today_key
    await send_admin_daily_report()


async def _check_downtime_on_startup():
    return None


async def main():
    dp.message.middleware(CommandRestrictionMiddleware())
    dp.callback_query.middleware(CommandRestrictionMiddleware())

    dp.include_router(settings_router)
    dp.include_router(demping_router)
    dp.include_router(certificates_router)
    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(rass_router)
    dp.include_router(playerok_router)
    dp.include_router(saveprofit_router)
    dp.include_router(wallet_router)
    dp.include_router(fp_admin_router)
    dp.include_router(tasks_router)
    dp.include_router(minprice_router)
    dp.include_router(ai_router)
    dp.include_router(ai_settings_router)

    job_defaults = {
        'coalesce': True,
        'max_instances': 1
    }
    scheduler = AsyncIOScheduler(job_defaults=job_defaults)

    scheduler.add_job(remind_unfilled_orders,        "cron",     hour=23, minute=40)
    scheduler.add_job(remind_unfilled_orders,        "cron",     hour=23, minute=55)
    scheduler.add_job(check_admin_daily_report_time, "cron",     minute="*")
    scheduler.start()

    print("[INFO] Запуск бота...")

    async def setup_bot_commands():
        await bot.set_my_commands([
            BotCommand(command="start", description="Главное меню"),
        ])

    @dp.callback_query(F.data.startswith("restart_mute_"))
    async def cb_restart_mute(call: types.CallbackQuery):
        minutes = int(call.data.split("_")[2])
        _set_mute(call.from_user.id, minutes)
        labels = {30: "30 минут", 60: "1 час", 1440: "1 день"}
        await call.answer(f"🔕 Уведомления отключены на {labels.get(minutes, f'{minutes} мин')}", show_alert=False)
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    async def notify_restart():
        from handlers.settings import is_enabled
        # Сразу при старте фиксируем даунтайм бота
        await _check_downtime_on_startup()

        restart_targets = [ADMIN_ID]
        for uid in restart_targets:
            if not is_enabled(uid, "restart_notify"):
                continue
            if _is_muted(uid):
                continue
            try:
                await bot.send_message(
                    uid,
                    "🔄 <b>Бот был перезагружен.</b>\n\n"
                    "Если вы были в каком-либо режиме (AI, расчёт, минпрайс и т.д.) — "
                    "введите команду заново.",
                    parse_mode="HTML",
                    reply_markup=_restart_mute_kb()
                )
            except Exception as e:
                print(f"[RESTART NOTICE] Не удалось отправить уведомление {uid}: {e}")
    await setup_bot_commands()

    async def post_start_notifications():
        notice = load_restart_notice()
        if notice:
            try:
                chat_id = int(notice.get("chat_id") or ADMIN_ID)
                text = notice.get("text") or "✅ <b>Бот успешно перезапустился.</b>"
                await bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception as e:
                print(f"[RESTART NOTICE] Не удалось отправить сообщение о перезапуске: {e}")
            finally:
                clear_restart_notice()

        await notify_restart()

    asyncio.create_task(post_start_notifications())

    while True:
        try:
            me = await bot.get_me()
            print(f"[INFO] Бот @{me.username} успешно запущен!")
            await dp.start_polling(bot, polling_timeout=2)
        except Exception as e:
            print(f"[CRITICAL ERROR] Вылет поллинга: {e}")
            print("[INFO] Попытка перезапуска через 5 секунд...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        print("[INFO] Бот остановлен вручную.")
    finally:
        loop.run_until_complete(bot.session.close())
        loop.close()
