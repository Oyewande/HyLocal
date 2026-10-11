from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from telegram.ext import Application

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from bot.handlers import register_handlers  # noqa: E402

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def build_app() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    register_handlers(app)
    return app


def run() -> None:
    logger.info("Starting HyLocal bot...")
    app = build_app()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
