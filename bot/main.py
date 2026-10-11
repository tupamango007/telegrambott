import asyncio
import logging

from aiogram import Bot, Dispatcher

from .ai import Responder
from .config import Config
from .handlers import create_router
from .storage import Storage


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    storage = Storage(config.db_path)
    responder = Responder(config.claude_model, config.claude_effort, config.persona_file)

    bot = Bot(config.telegram_token)
    dp = Dispatcher()
    dp.include_router(create_router(config, storage, responder))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
