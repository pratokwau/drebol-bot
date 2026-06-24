# handlers/start.py
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.enums import ParseMode

from loader import is_authorized
from config import ADMIN_ID
from handlers.utils import no_access_reply
router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    if not is_authorized(user_id):
        await no_access_reply(message)
        return

    is_admin = user_id == ADMIN_ID

    user_commands = (
        "<b>drebol</b> 🪼 приветствует вас!\n\n"
        "🛒 <b>FunPay / PlayerOK:</b>\n"
        "• /rassstart — Запуск расчёта\n"
        "• /playerokrass — Запуск PlayerOK расчёта\n"
        "• /saveprofit — Запись чистой прибыли\n\n"
        "🤖 <b>ИИ:</b>\n"
        "• /ai — AI-ассистент\n\n"
        "⚙️ <b>Прочее:</b>\n"
        "• /start — Главное меню\n"
        "• /about — О боте\n"
        "• /status — Просмотр работы бота\n"
        "• /help — Связаться с администратором\n"
        "• /settings — Настройки уведомлений\n"
        "• /cancel — Для выхода из текущего действия"
    )

    admin_commands = (
        "\n\n👨🏼‍💻 <b>Команды администратора:</b>\n"
        "• /admin — Админ-панель\n"
        "• /funpayauto — FunPay Auto\n"
    )

    text_welcome = user_commands + (admin_commands if is_admin else "")

    await message.answer(
        text_welcome,
        parse_mode=ParseMode.HTML
    )
