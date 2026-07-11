# handlers/rassstart.py

import os
import json
from typing import List

from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from states.states import ProfitCalc

router = Router()

COMMISSIONS_FILE = "data/commissions.json"
DIVIDER = "──────────────────"


def load_commissions() -> List[float]:
    if not os.path.exists(COMMISSIONS_FILE):
        return [3.0, 5.0, 10.0]
    try:
        with open(COMMISSIONS_FILE, "r", encoding="utf-8") as f:
            return [float(x) for x in json.load(f)]
    except Exception:
        return [3.0, 5.0, 10.0]


def save_commission(commission: float):
    commission = round(commission, 2)
    comm = load_commissions()
    if commission in comm:
        comm.remove(commission)
    comm.insert(0, commission)
    comm = comm[:5]
    os.makedirs(os.path.dirname(COMMISSIONS_FILE), exist_ok=True)
    with open(COMMISSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(comm, f, ensure_ascii=False, indent=2)


def commissions_keyboard() -> InlineKeyboardMarkup:
    comm = load_commissions()
    buttons = [InlineKeyboardButton(text=f"{c}%", callback_data=f"commission_{c}") for c in comm]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="rassstart_back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="rassstart_back_commission")]
    ])


def result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="rassstart_back_input")]
    ])


@router.message(Command("rassstart"))
async def rassstart_command(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(ProfitCalc.waiting_for_commission)
    await message.answer(
        "🧮 <b>Расчёт FunPay</b>\n\n"
        "💳 Выберите комиссию или введите вручную:",
        parse_mode=ParseMode.HTML,
        reply_markup=commissions_keyboard()
    )


@router.callback_query(F.data == "rassstart_back_main")
async def cb_rassstart_back_main(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    from handlers.start import start_menu_kb
    await call.message.edit_text(
        "🪼 <b>Drebol Bot</b>\n\nВыберите нужный раздел кнопкой ниже:",
        parse_mode=ParseMode.HTML,
        reply_markup=start_menu_kb()
    )
    await call.answer()


@router.callback_query(F.data.startswith("commission_"), StateFilter(ProfitCalc.waiting_for_commission))
async def choose_commission(call: types.CallbackQuery, state: FSMContext):
    commission = float(call.data.split("_")[1])
    save_commission(commission)
    await state.update_data(commission=commission)
    await state.set_state(ProfitCalc.waiting_for_input)
    await call.message.edit_text(
        f"✅ Комиссия: <b>{commission:.2f}%</b>\n\n"
        "Введите закупку и продажу через пробел:\n"
        "Пример: <code>1500 2200</code>"
        ,
        parse_mode=ParseMode.HTML,
        reply_markup=input_keyboard()
    )
    await call.answer()


@router.callback_query(F.data == "rassstart_back_commission")
async def cb_rassstart_back_commission(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfitCalc.waiting_for_commission)
    await call.message.edit_text(
        "🧮 <b>Расчёт FunPay</b>\n\n"
        "💳 Выберите комиссию или введите вручную:",
        parse_mode=ParseMode.HTML,
        reply_markup=commissions_keyboard()
    )
    await call.answer()


@router.message(StateFilter(ProfitCalc.waiting_for_commission))
async def get_commission(message: types.Message, state: FSMContext):
    try:
        commission = float(message.text.strip().replace(",", "."))
        if commission <= 0:
            raise ValueError
    except ValueError:
        return await message.answer(
            "⚠️ Введите корректный процент комиссии\n"
            "Пример: <code>3</code> или <code>4.5</code>",
            parse_mode=ParseMode.HTML
        )

    save_commission(commission)
    await state.update_data(commission=commission)
    await state.set_state(ProfitCalc.waiting_for_input)
    await message.answer(
        f"✅ Комиссия: <b>{commission:.2f}%</b>\n\n"
        "Введите закупку и продажу через пробел:\n"
        "Пример: <code>1500 2200</code>"
        ,
        parse_mode=ParseMode.HTML,
        reply_markup=input_keyboard()
    )


@router.message(StateFilter(ProfitCalc.waiting_for_input))
async def calc_profit(message: types.Message, state: FSMContext):
    parts = [p.replace(",", ".") for p in message.text.strip().split()]

    if len(parts) != 2:
        return await message.answer(
            "⚠️ Нужно ввести две суммы через пробел\n"
            "Пример: <code>1000 1500</code>",
            parse_mode=ParseMode.HTML
        )

    try:
        buy_price = float(parts[0])
        sell_price = float(parts[1])
        if buy_price <= 0 or sell_price <= 0:
            raise ValueError
    except ValueError:
        return await message.answer(
            "⚠️ Введите корректные положительные числа",
            parse_mode=ParseMode.HTML
        )

    data = await state.get_data()
    commission = data.get("commission", 3.0)
    profit = sell_price - buy_price - (sell_price * commission / 100)

    await message.answer(
        f"📊 <b>Результат FunPay</b>\n\n"
        f"🛒 Закупка:     <b>{buy_price:,.2f} ₽</b>\n"
        f"💰 Продажа:     <b>{sell_price:,.2f} ₽</b>\n"
        f"🏦 Комиссия:    <b>{commission:.2f}%</b>\n"
        f"{DIVIDER}\n"
        f"💎 <b>Чистая прибыль: {profit:,.2f} ₽</b>\n",
        parse_mode=ParseMode.HTML,
        reply_markup=result_keyboard()
    )


@router.callback_query(F.data == "rassstart_back_input")
async def cb_rassstart_back_input(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    commission = data.get("commission", 3.0)
    await state.set_state(ProfitCalc.waiting_for_input)
    await call.message.edit_text(
        f"✅ Комиссия: <b>{float(commission):.2f}%</b>\n\n"
        "Введите закупку и продажу через пробел:\n"
        "Пример: <code>1500 2200</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=input_keyboard()
    )
    await call.answer()
