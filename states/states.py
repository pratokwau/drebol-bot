# states/states.py
from aiogram.fsm.state import State, StatesGroup


class ProfitCalc(StatesGroup):
    waiting_for_commission = State()
    waiting_for_input = State()


class SaveProfitStates(StatesGroup):
    choose_type = State()
    waiting_funpay_prices = State()
    waiting_sale_c = State()
    waiting_withdraw_c = State()
    waiting_playerok_prices = State()
    choose_edit = State()
    edit_choose_type = State()
    edit_waiting_funpay_prices = State()
    edit_waiting_sale_c = State()
    edit_waiting_withdraw_c = State()
    edit_waiting_playerok_prices = State()
    choose_delete = State()


class ProfitStatsStates(StatesGroup):
    waiting_custom_period = State()


class TaskUnfilledStates(StatesGroup):
    waiting_custom_period = State()
