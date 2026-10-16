"""Точка входа СИМ-Рулетки. Запуск: BOT_TOKEN=... python bot.py"""
import asyncio
import sys

from simbot.config import Config
from simbot.core import Bot

# регистрация обработчиков (порядок не важен, но все модули должны быть импортированы)
import simbot.handlers_user  # noqa: F401,E402
import simbot.handlers_spin  # noqa: F401,E402
import simbot.handlers_games  # noqa: F401,E402
import simbot.handlers_duel  # noqa: F401,E402
import simbot.handlers_market  # noqa: F401,E402
import simbot.handlers_clan  # noqa: F401,E402
import simbot.handlers_quiz  # noqa: F401,E402
import simbot.handlers_admin  # noqa: F401,E402


def main() -> None:
    cfg = Config()
    bot = Bot(cfg)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if sys.version_info < (3, 10):
        raise SystemExit("Нужен Python 3.10+")
    main()
