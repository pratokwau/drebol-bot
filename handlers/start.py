from aiogram import Router, types
from aiogram.filters import Command
from aiogram.enums import ParseMode

router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "<b>🪼 Drebol Bot</b>\n\n"
        "📋 <b>Список команд:</b>\n"
        "• /rassstart — Расчет прибыли FunPay\n"
        "• /playerokrass — Расчет прибыли PlayerOK\n"
        "• /saveprofit — Сохранение прибыли\n"
        "• /ai — AI-помощник\n"
        "• /settings — Настройки уведомлений\n"
        "• /status — Статус бота\n"
        "• /cancel — Отменить текущее действие\n"
        "• /admin — Панель администратора\n"
        "• /funpayauto — Автообновление FunPay"
    )

    await message.answer(text, parse_mode=ParseMode.HTML)
