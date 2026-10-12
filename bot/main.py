from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from telegram import BotCommand
from telegram.ext import Application

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from bot.handlers import register_handlers  # noqa: E402

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Commands shown in the Telegram "/" menu for regular users
USER_COMMANDS = [
    BotCommand("start",   "Get started with HyLocal"),
    BotCommand("weekend", "See what's on this weekend"),
    BotCommand("search",  "Search events by keyword"),
    BotCommand("prefs",   "Set your interests & filters"),
    BotCommand("help",    "List all commands"),
]


def build_app() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    register_handlers(app)
    return app


async def _register_commands(app: Application) -> None:
    """Set the bot command menu shown in the Telegram "/" drawer."""
    try:
        await app.bot.set_my_commands(USER_COMMANDS)
        logger.info(f"Bot commands registered: {[c.command for c in USER_COMMANDS]}")
    except Exception as e:
        logger.warning(f"Could not register bot commands: {e}")


def run() -> None:
    logger.info("Starting HyLocal bot...")
    app = build_app()

    async def _post_init(application: Application) -> None:
        await _register_commands(application)

    app.post_init = _post_init
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
