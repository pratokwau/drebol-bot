# middlewares/command_restriction.py

from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from aiogram.enums import ParseMode

from handlers.utils import (
    is_command_allowed,
)
from config import ADMIN_ID


class CommandRestrictionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        # Проверяем только сообщения с командами
        if isinstance(event, Message) and event.text and event.text.startswith("/"):
            user_id = event.from_user.id

            # Админа не ограничиваем
            if user_id == ADMIN_ID:
                return await handler(event, data)

            # Извлекаем команду (убираем @botname если есть)
            command = event.text.split()[0].split("@")[0].lower()

            # Стандартная проверка ограничений для остальных
            if not is_command_allowed(user_id, command):
                await event.answer(
                    f"⛔ У вас нет доступа к команде <code>{command}</code>",
                    parse_mode=ParseMode.HTML
                )
                return  # Не передаём дальше обработчику

        return await handler(event, data)
