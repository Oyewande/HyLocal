from __future__ import annotations

"""
HyLocal — main entrypoint.
Runs the Telegram bot and the scheduler in parallel.

Usage:
    python main.py
"""

import asyncio
import threading
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).resolve().parent / ".env")

from bot.main import build_app  # noqa: E402
from scheduler.main import build_scheduler, run_scrape_job  # noqa: E402


def start_scheduler() -> None:
    """Run the scheduler in a background thread."""
    scheduler = build_scheduler()
    logger.info("Scheduler running in background thread")
    scheduler.start()


async def start_bot() -> None:
    """Run the Telegram bot (async, main thread)."""
    app = build_app()
    logger.info("Bot running...")
    await app.run_polling(drop_pending_updates=True)


def main() -> None:
    # Start scheduler in background thread
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    # Run bot in main thread (blocking)
    asyncio.run(start_bot())


if __name__ == "__main__":
    main()
