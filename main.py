from __future__ import annotations

import asyncio
import time

from aiogram.types import BotCommand
from config import ADMIN_ID
from handlers.admin import router as admin_router
from handlers.cancel import router as cancel_router
from handlers.start import router as start_router
from handlers.tickets import router as tickets_router
from loader import bot, dp
from storage import clear_update_state, load_update_state
from sub.adminpaysub.paid_settings_store import DEFAULT_PAID_PAYMENT_URL
from sub.adminpaysub.paid_storage import (
    load_paid_subscriptions,
    paid_subscription_status,
    refresh_paid_subscription_state,
    save_paid_subscriptions,
)
from sub.adminpaysub.paid_subscriptions import _sync_paid_user_devices_expiry
from sub import router as xui_router
from sub.adminpaysub.paid_settings_store import format_duration


def _payment_notification_started_key(event: str) -> str:
    return {
        "trial_expired": "trial_expired_notification_started_at",
        "payment_expired": "payment_expired_notification_started_at",
        "grace_expired": "grace_expired_notification_started_at",
    }[event]


def _payment_notification_sent_key(event: str) -> str:
    return {
        "trial_expired": "trial_expired_notified_at",
        "payment_expired": "payment_expired_notified_at",
        "grace_expired": "grace_expired_notified_at",
    }[event]


async def setup_commands() -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
        ]
    )


async def _notify_about_paid_subscriptions() -> None:
    while True:
        try:
            subscriptions = load_paid_subscriptions()
            changed = False
            for user_key, info in list(subscriptions.items()):
                if not isinstance(info, dict) or not str(user_key).isdigit():
                    continue
                refreshed, events = refresh_paid_subscription_state(info)
                if refreshed is not info:
                    subscriptions[user_key] = refreshed
                    changed = True
                if not events:
                    continue
                user_id = int(user_key)
                for event in events:
                    started_key = _payment_notification_started_key(event)
                    sent_key = _payment_notification_sent_key(event)
                    refreshed[started_key] = int(time.time())
                    subscriptions[user_key] = refreshed
                    save_paid_subscriptions(subscriptions)
                    if event == "trial_expired":
                        grace_ends_at = int(refreshed.get("grace_ends_at") or 0)
                        grace_text = format_duration(refreshed.get("grace_seconds"))
                        if grace_ends_at:
                            await _sync_paid_user_devices_expiry(
                                user_id,
                                grace_ends_at * 1000,
                                enabled=True,
                                limit_ip=int(refreshed.get("limit_ip") or 0),
                                limit_gb=float(refreshed.get("limit_gb") or 0),
                                flow=str(refreshed.get("flow") or ""),
                            )
                        await bot.send_message(
                            user_id,
                            "🧪 <b>Пробный период истёк.</b>\n\n"
                            f"Доступ сохранён ещё на {grace_text}.\n"
                            "Если за это время не оплатить, доступ будет удалён.\n"
                            "Открой /sub и нажми «Продлить подписку».",
                            parse_mode="HTML",
                        )
                        refreshed[sent_key] = int(time.time())
                    elif event == "payment_expired":
                        grace_text = format_duration(refreshed.get("grace_seconds"))
                        grace_ends_at = int(refreshed.get("grace_ends_at") or 0)
                        if grace_ends_at:
                            await _sync_paid_user_devices_expiry(
                                user_id,
                                grace_ends_at * 1000,
                                enabled=False,
                                limit_ip=int(refreshed.get("limit_ip") or 0),
                                limit_gb=float(refreshed.get("limit_gb") or 0),
                                flow=str(refreshed.get("flow") or ""),
                            )
                        await bot.send_message(
                            user_id,
                            "⏳ <b>Срок подписки истёк.</b>\n\n"
                            f"У тебя есть {grace_text}, чтобы продлить оплату.\n"
                            + "Открой /sub и нажми «Продлить подписку».",
                            parse_mode="HTML",
                        )
                        refreshed[sent_key] = int(time.time())
                    elif event == "grace_expired":
                        grace_ends_at = int(refreshed.get("grace_ends_at") or 0)
                        if grace_ends_at:
                            await _sync_paid_user_devices_expiry(
                                user_id,
                                grace_ends_at * 1000,
                                limit_ip=int(refreshed.get("limit_ip") or 0),
                                limit_gb=float(refreshed.get("limit_gb") or 0),
                                flow=str(refreshed.get("flow") or ""),
                            )
                        await bot.send_message(
                            user_id,
                            "⛔ <b>Период продления закончился.</b>\n\n"
                            "Подписка сохранена в системе.\n"
                            "Чтобы вернуть доступ, нажми /sub и продли подписку.",
                            parse_mode="HTML",
                        )
                        refreshed[sent_key] = int(time.time())
                    refreshed.pop(started_key, None)
                    subscriptions[user_key] = refreshed
                    save_paid_subscriptions(subscriptions)
                changed = True
            if changed:
                save_paid_subscriptions(subscriptions)
        except Exception:
            pass
        await asyncio.sleep(10)


async def main() -> None:
    dp.include_router(cancel_router)
    dp.include_router(start_router)
    dp.include_router(tickets_router)
    dp.include_router(admin_router)
    dp.include_router(xui_router)
    await setup_commands()

    async def notify_update_success() -> None:
        state = load_update_state()
        if state.get("status") != "pending_success":
            return
        chat_id = int(state.get("chat_id") or ADMIN_ID or 0)
        if not chat_id:
            return
        await asyncio.sleep(2)
        try:
            await bot.send_message(
                chat_id,
                "✅ <b>Обновление применено успешно.</b>\n\n"
                "Новая версия бота запущена и готова к работе.",
            )
        finally:
            clear_update_state()

    asyncio.create_task(notify_update_success())
    asyncio.create_task(_notify_about_paid_subscriptions())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
