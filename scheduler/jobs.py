from __future__ import annotations

import asyncio
import os

from loguru import logger
from telegram import Bot
from telegram.constants import ParseMode

from bot.formatter import format_digest
from processor.pipeline import Pipeline
from scraper.runner import run_all_scrapers
from storage import db

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


async def scrape_and_process() -> None:
    """
    Job 1 — runs several times a week (e.g. Mon, Wed, Fri).
    Collects raw signals, extracts events, stores them.
    """
    logger.info("=== Scrape & process job starting ===")
    try:
        signals = await run_all_scrapers()
        if not signals:
            logger.info("No signals collected — nothing to process")
            return

        pipeline = Pipeline()
        events = pipeline.process(signals)

        stored = 0
        for event in events:
            result = db.upsert_event(event)
            if result:
                stored += 1

        logger.info(f"=== Scrape complete: {stored} events stored from {len(signals)} signals ===")
    except Exception as e:
        logger.error(f"Scrape & process job failed: {e}")


def send_friday_digest() -> None:
    """
    Job 2 — runs every Friday at 4pm Lagos time (UTC+1).
    Sends the weekend digest to all active users.
    """
    logger.info("=== Friday digest job starting ===")

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set — cannot send digest")
        return

    events = db.get_approved_events(limit=7)
    digest = format_digest(events)
    users = db.get_all_active_users()

    if not users:
        logger.warning("No active users to send digest to")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    async def _send_all() -> None:
        sent = 0
        failed = 0
        for user in users:
            try:
                await bot.send_message(
                    chat_id=user["telegram_id"],
                    text=digest,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                sent += 1
            except Exception as e:
                logger.warning(f"Failed to send digest to {user['telegram_id']}: {e}")
                failed += 1

        logger.info(f"=== Digest sent: {sent} delivered, {failed} failed ===")

    asyncio.run(_send_all())
