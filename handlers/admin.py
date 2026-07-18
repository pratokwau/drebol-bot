import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from config import ADMIN_ID
from update_manager import (
    get_update_status,
    update_from_git,
    restart_service,
    save_admin_chat_id,
    save_restart_notice,
)

router = Router()
DATA_DIR = Path("data")


class AdminDatabaseStates(StatesGroup):
    waiting_upload = State()


def _data_export_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if DATA_DIR.exists():
            for path in DATA_DIR.rglob("*"):
                if not path.is_file():
                    continue
                if path.name.endswith(".pyc") or "__pycache__" in path.parts:
                    continue
                archive.write(path, arcname=path.relative_to(DATA_DIR))
    return buffer.getvalue()


def _safe_extract_zip(zip_path: Path, destination: Path) -> int:
    extracted = 0
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            member_path = Path(member.filename)
            if member_path.is_absolute():
                continue
            parts = member_path.parts
            if not parts:
                continue
            if parts[0] == "data":
                parts = parts[1:]
            if not parts:
                continue
            if any(part == ".." for part in parts):
                continue
            target_path = destination.joinpath(*parts)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target_path, "wb") as target:
                shutil.copyfileobj(source, target)
            extracted += 1
    return extracted


def _database_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Выгрузка базы данных", callback_data="admin_db_export")],
            [InlineKeyboardButton(text="📥 Загрузка базы данных", callback_data="admin_db_import")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back_main")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    update_label, _ = get_update_status()
    update_text = f"🔄 Обновиться ({update_label})"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=update_text, callback_data="admin_update")
            ],
            [
                InlineKeyboardButton(text="🤖 AI ключи", callback_data="admin_ai_settings")
            ],
            [
                InlineKeyboardButton(text="🗄 База данных", callback_data="admin_database")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back_main")
            ],
        ]
    )


@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    update_label, _ = get_update_status()

    await message.answer(
        f"👨🏼‍💻 <b>Админ-Панель</b>\n\n"
        f"Обновление: <b>{update_label}</b>\n\n"
        f"Выберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu()
    )


@router.callback_query(F.data == "back_to_admin")
async def cb_back_to_admin(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        f"👨🏼‍💻 <b>Админ-панель</b>\n\n"
        f"Обновление: <b>{get_update_status()[0]}</b>\n\n"
        f"Выберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_back_main")
async def cb_admin_back_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    from handlers.start import start_menu_kb

    await callback.message.edit_text(
        "🪼 <b>Drebol Bot</b>\n\n"
        "Выберите нужный раздел кнопкой ниже:",
        parse_mode=ParseMode.HTML,
        reply_markup=start_menu_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_update")
async def cb_admin_update(callback: types.CallbackQuery):
    status, has_update = get_update_status()
    if not has_update:
        await callback.answer("Новая версия не найдена", show_alert=True)
        return

    await callback.message.edit_text(
        "🔄 <b>Обновление запущено</b>\n\n"
        "Сейчас я подтяну код из репозитория, не трогая базы и данные.\n"
        "После этого сервис перезапустится сам.",
        parse_mode=ParseMode.HTML,
    )
    ok, msg = update_from_git()
    if not ok:
        await callback.message.edit_text(
            f"❌ <b>Ошибка обновления</b>\n\n<code>{msg}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_admin_keyboard()
        )
        return

    try:
        save_admin_chat_id(callback.message.chat.id)
        save_restart_notice(
            callback.message.chat.id,
            "✅ <b>Бот успешно перезапустился.</b>\n\nОбновление применено успешно."
        )
        restart_service()
        await callback.message.edit_text(
            "✅ <b>Обновление установлено</b>\n\n"
            "Сервис перезапускается, это может занять до минуты.\n"
            "После старта я пришлю подтверждение отдельным сообщением.",
            parse_mode=ParseMode.HTML,
        )
    finally:
        await callback.answer("Обновление выполнено", show_alert=False)


def back_to_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_admin")]
        ]
    )


@router.callback_query(F.data == "admin_database")
async def cb_admin_database(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🗄 <b>База данных</b>\n\n"
        "Здесь можно выгрузить весь каталог `data` в ZIP или загрузить обратно JSON/ZIP-файлы.",
        parse_mode=ParseMode.HTML,
        reply_markup=_database_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_db_export")
async def cb_admin_db_export(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    payload = _data_export_bytes()
    filename = f"drebolbot-data-backup-{callback.message.date:%Y%m%d-%H%M%S}.zip"
    await callback.message.answer_document(
        BufferedInputFile(payload, filename=filename),
        caption="✅ <b>База данных выгружена.</b>\n\nЭто архив папки <code>data</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=_database_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_db_import")
async def cb_admin_db_import(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminDatabaseStates.waiting_upload)
    await callback.message.edit_text(
        "📥 <b>Загрузка базы данных</b>\n\n"
        "Отправьте <code>.json</code> или <code>.zip</code> файл.\n"
        "Можно загружать файлы по одному, либо одним ZIP-архивом с несколькими файлами.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_database")]
        ]),
    )
    await callback.answer()


@router.message(AdminDatabaseStates.waiting_upload, F.document)
async def proc_admin_db_upload(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    document = message.document
    if not document or not document.file_name:
        return await message.answer("⚠️ Не удалось прочитать файл.")

    file_name = Path(document.file_name).name
    lower_name = file_name.lower()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if lower_name.endswith(".zip"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            temp_path = Path(tmp.name)
        try:
            await message.bot.download(document, destination=temp_path)
            imported = _safe_extract_zip(temp_path, DATA_DIR)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

        await message.answer(
            f"✅ Загружен ZIP-архив.\nИмпортировано файлов: <b>{imported}</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_database")]
            ]),
        )
        return

    if not lower_name.endswith(".json"):
        return await message.answer(
            "⚠️ Принимаются только <code>.json</code> или <code>.zip</code> файлы.",
            parse_mode=ParseMode.HTML,
        )

    target_path = DATA_DIR / file_name
    await message.bot.download(document, destination=target_path)
    await message.answer(
        f"✅ Файл <code>{file_name}</code> сохранён в <code>data</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_database")]
        ]),
    )


@router.message(AdminDatabaseStates.waiting_upload)
async def proc_admin_db_upload_text(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("⚠️ Отправьте файл <code>.json</code> или <code>.zip</code>.", parse_mode=ParseMode.HTML)
