"""Точка входа для хостингов, которые запускают main.py из корня репозитория."""

import asyncio

from bot.main import main

if __name__ == "__main__":
    asyncio.run(main())
