# handlers/settings.py

import os
import json
import re
import sqlite3

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from config import ADMIN_ID
from base_store import user_db_path, admin_db_path, connect, ensure_dir

router = Router()

DEFAULTS = {
    "restart_notify": False,
    "broadcast_notify": True,
    "admin_report_notify": True,
    "admin_report_time": "23:59",
    "tts_voice": "ru-RU-SvetlanaNeural",
}

TTS_VOICES = {
    "ru-RU-SvetlanaNeural": "🔊 Светлана (жен.)",
    "ru-RU-DmitryNeural": "🔊 Дмитрий (муж.)",
}

SETTINGS_FILE = "data/user_settings.json"


class SettingsStates(StatesGroup):
    waiting_admin_report_time = State()


def _db_path(uid: int) -> str:
    return user_db_path(uid, "settings")


def _conn(uid: int) -> sqlite3.Connection:
    return connect(_db_path(uid))


def _create_tables(uid: int):
    c = _conn(uid).cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY, value TEXT)''')
    _conn(uid).commit()


def _get_raw(uid: int) -> dict:
    _create_tables(uid)
    c = _conn(uid).cursor()
    c.execute("SELECT key, value FROM settings")
    return {k: v for k, v in c.fetchall()}


def _set_raw(uid: int, key: str, value):
    _create_tables(uid)
    c = _conn(uid).cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))
    _conn(uid).commit()


def _decode_value(raw):
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _maybe_migrate_json():
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    for uid_str, s in data.items():
        try:
            uid = int(uid_str)
        except Exception:
            continue
        if not isinstance(s, dict):
            continue
        for k, v in s.items():
            _set_raw(uid, k, v)


_maybe_migrate_json()


def load_all() -> dict:
    return {str(ADMIN_ID): get_user_settings(ADMIN_ID)}


def get_user_settings(uid: int) -> dict:
    data = dict(DEFAULTS)
    raw = _get_raw(uid)
    for k, v in raw.items():
        data[k] = _decode_value(v)
    return data


def update_setting(uid: int, key: str, value):
    _set_raw(uid, key, value)


def is_enabled(uid: int, key: str) -> bool:
    return get_user_settings(uid).get(key, DEFAULTS.get(key, True))


def _on_off(val: bool) -> str:
    return "✅ Вкл" if val else "❌ Выкл"


def settings_kb(uid: int) -> InlineKeyboardMarkup:
    s = get_user_settings(uid)
    voice_label = TTS_VOICES.get(s["tts_voice"], s["tts_voice"])
    rows = [
        [InlineKeyboardButton(text=f"🔄 Перезагрузка бота: {_on_off(s['restart_notify'])}", callback_data="stg_toggle_restart_notify")],
    ]
    if int(uid) == int(ADMIN_ID):
        rows.extend([
            [InlineKeyboardButton(text=f"🌙 Админ-отчёт: {_on_off(s['admin_report_notify'])}", callback_data="stg_toggle_admin_report_notify")],
            [InlineKeyboardButton(text=f"🕛 Время админ-отчёта: {s['admin_report_time']}", callback_data="stg_set_admin_report_time")],
        ])
    rows.append([InlineKeyboardButton(text=f"🎙 Голос ИИ: {voice_label}", callback_data="stg_voice_menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stg_main_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def has_settings_access(uid: int) -> bool:
    return True


def parse_time_value(text: str) -> str | None:
    text = (text or "").strip()
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        return None
    h, m = map(int, text.split(":"))
    if h > 23 or m > 59:
        return None
    return f"{h:02d}:{m:02d}"


@router.message(Command("settings"))
async def cmd_settings(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await state.clear()
    kb = settings_kb(uid)
    await message.answer("⚙️ <b>Настройки уведомлений</b>\n\nНажмите на пункт, чтобы переключить:", parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data.startswith("stg_toggle_"))
async def cb_toggle(call: types.CallbackQuery):
    uid = call.from_user.id
    key = call.data.replace("stg_toggle_", "")
    current = is_enabled(uid, key)
    update_setting(uid, key, not current)
    kb = settings_kb(uid)
    await call.message.edit_reply_markup(reply_markup=kb)
    await call.answer("Включено" if not current else "Выключено")


@router.callback_query(F.data == "stg_set_admin_report_time")
async def cb_set_admin_report_time(call: types.CallbackQuery, state: FSMContext):
    s = get_user_settings(call.from_user.id)
    await state.set_state(SettingsStates.waiting_admin_report_time)
    await call.message.answer(
        f"🕛 <b>Введите время админ-отчёта</b>\n\nТекущее: <b>{s['admin_report_time']}</b>\n\nФормат: <code>ЧЧ:ММ</code> (например <code>23:59</code>)",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="stg_cancel_time")]])
    )
    await call.answer()


@router.callback_query(F.data == "stg_cancel_time")
async def cb_cancel_time(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.answer("Назад")


@router.callback_query(F.data == "stg_voice_menu")
async def cb_voice_menu(call: types.CallbackQuery):
    s = get_user_settings(call.from_user.id)
    current = s["tts_voice"]
    buttons = []
    for voice_id, label in TTS_VOICES.items():
        mark = "✅ " if voice_id == current else ""
        buttons.append([InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"stg_voice_{voice_id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stg_back")])
    await call.message.edit_text("🎙 <b>Выберите голос для озвучки ИИ:</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@router.callback_query(F.data.startswith("stg_voice_ru"))
async def cb_set_voice(call: types.CallbackQuery):
    voice_id = call.data.replace("stg_voice_", "")
    if voice_id not in TTS_VOICES:
        return await call.answer("Неизвестный голос", show_alert=True)
    update_setting(call.from_user.id, "tts_voice", voice_id)
    label = TTS_VOICES[voice_id]
    await call.answer(f"Голос изменён: {label}")
    await call.message.edit_text("⚙️ <b>Настройки уведомлений</b>\n\nНажмите на пункт, чтобы переключить:", parse_mode=ParseMode.HTML, reply_markup=settings_kb(call.from_user.id))


@router.callback_query(F.data == "stg_back")
async def cb_stg_back(call: types.CallbackQuery):
    await call.message.edit_text("⚙️ <b>Настройки уведомлений</b>\n\nНажмите на пункт, чтобы переключить:", parse_mode=ParseMode.HTML, reply_markup=settings_kb(call.from_user.id))
    await call.answer()


@router.callback_query(F.data == "stg_main_back")
async def cb_stg_main_back(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    from handlers.start import start_menu_kb
    await call.message.edit_text(
        "🪼 <b>Drebol Bot</b>\n\nВыберите нужный раздел кнопкой ниже:",
        parse_mode=ParseMode.HTML,
        reply_markup=start_menu_kb()
    )
    await call.answer()


@router.message(SettingsStates.waiting_admin_report_time)
async def proc_set_admin_report_time(message: types.Message, state: FSMContext):
    if int(message.from_user.id) != int(ADMIN_ID):
        await state.clear()
        return
    time_str = parse_time_value(message.text)
    if not time_str:
        return await message.answer("⚠️ Неверный формат. Введите время как <code>ЧЧ:ММ</code>, например <code>23:59</code>", parse_mode=ParseMode.HTML)
    update_setting(message.from_user.id, "admin_report_time", time_str)
    await state.clear()
    await message.answer(f"✅ Время админ-отчёта установлено: <b>{time_str}</b>", parse_mode=ParseMode.HTML, reply_markup=settings_kb(message.from_user.id))
