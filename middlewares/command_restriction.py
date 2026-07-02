# middlewares/command_restriction.py

from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message


class CommandRestrictionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        # Middleware больше не нужен, так как система пользователей удалена
        # Просто пропускаем все сообщения
        return await handler(event, data)
