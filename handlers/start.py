from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_ID
from states.states import ProfitCalc, SaveProfitStates
from states.playerokrass_states import PlayerOkStates
from handlers.rassstart import commissions_keyboard
from handlers.playerokrass import commission_keyboard
from handlers.saveprofit import saveprofit_menu_text, saveprofit_menu_keyboard
from handlers.ai_chat import _ai_is_configured, _ai_not_configured_text, load_chats, chats_list_kb, _start_new_chat
from handlers.settings import settings_kb
from handlers.admin import admin_menu
from update_manager import get_update_status
from handlers.funpay_admin import show_funpay_auto_menu
from handlers.cancel import cmd_cancel

router = Router()


def start_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧮 Расчёт FunPay", callback_data="start_rassstart"),
            InlineKeyboardButton(text="🟩 Расчёт PlayerOK", callback_data="start_playerokrass"),
        ],
        [
            InlineKeyboardButton(text="💼 Прибыль", callback_data="start_saveprofit"),
            InlineKeyboardButton(text="🤖 AI", callback_data="start_ai"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="start_settings"),
        ],
        [
            InlineKeyboardButton(text="👨🏼‍💻 Админ-панель", callback_data="start_admin"),
            InlineKeyboardButton(text="🛸 FunPay Auto", callback_data="start_funpayauto"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="start_cancel"),
        ],
    ])


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "<b>🪼 Drebol Bot</b>\n\n"
        "Выберите нужный раздел кнопкой ниже:"
    )

    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=start_menu_kb())


@router.callback_query(F.data == "start_rassstart")
async def cb_start_rassstart(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfitCalc.waiting_for_commission)
    await call.message.edit_text(
        "🧮 <b>Расчёт FunPay</b>\n\n"
        "💳 Выберите комиссию или введите вручную:",
        parse_mode=ParseMode.HTML,
        reply_markup=commissions_keyboard()
    )
    await call.answer()


@router.callback_query(F.data == "start_playerokrass")
async def cb_start_playerokrass(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(PlayerOkStates.waiting_sale_commission)
    await call.message.edit_text(
        "🧮 <b>Расчёт PlayerOK</b>\n\n"
        "💳 Комиссия на продажу (%):",
        parse_mode=ParseMode.HTML,
        reply_markup=commission_keyboard("sale", "sale_commission")
    )
    await call.answer()


@router.callback_query(F.data == "start_saveprofit")
async def cb_start_saveprofit(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        saveprofit_menu_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=saveprofit_menu_keyboard()
    )
    await call.answer()


@router.callback_query(F.data == "start_ai")
async def cb_start_ai(call: types.CallbackQuery, state: FSMContext):
    if not _ai_is_configured():
        await call.message.edit_text(_ai_not_configured_text(), parse_mode=ParseMode.HTML)
        await call.answer()
        return

    await state.clear()
    if call.message.chat.type in ("group", "supergroup"):
        await _start_new_chat(call, state, call.from_user.id, is_call=True)
        return

    chats = load_chats(call.from_user.id)
    if not chats:
        await _start_new_chat(call, state, call.from_user.id, is_call=True)
        return

    await call.message.edit_text(
        "🤖 <b>AI Ассистент</b>\n\nВыберите чат или создайте новый:",
        parse_mode=ParseMode.HTML,
        reply_markup=chats_list_kb(chats)
    )
    await call.answer()


@router.callback_query(F.data == "start_settings")
async def cb_start_settings(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "⚙️ <b>Настройки уведомлений</b>\n\nНажмите на пункт, чтобы переключить:",
        parse_mode=ParseMode.HTML,
        reply_markup=settings_kb(call.from_user.id)
    )
    await call.answer()


@router.callback_query(F.data == "start_admin")
async def cb_start_admin(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    update_label, _ = get_update_status()
    await call.message.edit_text(
        f"👨🏼‍💻 <b>Админ-Панель</b>\n\n"
        f"Обновление: <b>{update_label}</b>\n\n"
        f"Выберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu()
    )
    await call.answer()


@router.callback_query(F.data == "start_funpayauto")
async def cb_start_funpayauto(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_funpay_auto_menu(call)


@router.callback_query(F.data == "start_cancel")
async def cb_start_cancel(call: types.CallbackQuery, state: FSMContext):
    await cmd_cancel(call.message, state)
    await call.answer()
