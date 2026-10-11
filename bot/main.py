import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import Config
from .handlers import create_router
from .replies import TemplateStore
from .storage import Storage


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    storage = Storage(config.db_path)
    templates = TemplateStore(config.replies_file)

    bot = Bot(config.telegram_token)
    dp = Dispatcher()
    dp.include_router(create_router(config, storage, templates))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
