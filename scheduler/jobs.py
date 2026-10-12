from __future__ import annotations

import asyncio
import os
import traceback

from loguru import logger
from telegram import Bot
from telegram.constants import ParseMode

from bot.formatter import feedback_keyboard, format_event
from processor.pipeline import Pipeline
from scraper.runner import run_all_scrapers
from storage import db

TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
CURATOR_GROUP_ID      = int(os.getenv("TELEGRAM_CURATOR_GROUP_ID") or "0")


async def _alert_curator_group(bot: Bot, job_name: str, error: Exception) -> None:
    """Send a failure alert to the curator group so issues don't go unnoticed."""
    if not CURATOR_GROUP_ID:
        return
    tb = traceback.format_exc()[-800:]  # last 800 chars of traceback
    text = (
        f"🚨 <b>HyLocal job failed: {job_name}</b>\n\n"
        f"<code>{type(error).__name__}: {error}</code>\n\n"
        f"<pre>{tb}</pre>"
    )
    try:
        await bot.send_message(
            chat_id=CURATOR_GROUP_ID,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Could not send alert to curator group: {e}")


async def scrape_and_process() -> None:
    """
    Job 1 — runs several times a week (e.g. Mon, Wed, Fri).
    Collects raw signals, extracts events, stores them.
    """
    logger.info("=== Scrape & process job starting ===")
    bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
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

        # Notify curator group when new events land in the queue
        if bot and stored and CURATOR_GROUP_ID:
            await bot.send_message(
                chat_id=CURATOR_GROUP_ID,
                text=f"📥 Scrape complete — <b>{stored} new event(s)</b> in the review queue.\nUse /queue to review.",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.error(f"Scrape & process job failed: {e}")
        if bot:
            await _alert_curator_group(bot, "scrape_and_process", e)


def send_friday_digest() -> None:
    """
    Job 2 — runs every Friday at 4pm Lagos time (UTC+1).
    Sends the weekend digest to all active users, personalised by their prefs,
    with 👍/👎 feedback buttons on each event.
    """
    logger.info("=== Friday digest job starting ===")

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set — cannot send digest")
        return

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
                # Load personalised events for this user
                prefs = db.get_user_preferences(user["telegram_id"]) or {}
                events = db.get_approved_events(
                    limit=7,
                    categories=prefs.get("categories") or None,
                    max_budget=prefs.get("max_budget"),
                    neighbourhoods=prefs.get("neighbourhoods") or None,
                    vibes=prefs.get("vibes") or None,
                    is_free_only=bool(prefs.get("is_free_only", False)),
                    time_of_day=prefs.get("time_of_day") or None,
                )
                # Fall back to unfiltered if prefs match nothing
                if not events:
                    events = db.get_approved_events(limit=7)
                if not events:
                    continue

                await bot.send_message(
                    chat_id=user["telegram_id"],
                    text="🌆 <b>What's happening around you this weekend</b>",
                    parse_mode=ParseMode.HTML,
                )
                for i, event in enumerate(events, 1):
                    text = format_event(event, index=i)
                    kb   = feedback_keyboard(event["id"])
                    await bot.send_message(
                        chat_id=user["telegram_id"],
                        text=text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                        disable_web_page_preview=True,
                    )
                sent += 1
            except Exception as e:
                logger.warning(f"Failed to send digest to {user['telegram_id']}: {e}")
                failed += 1

        logger.info(f"=== Digest sent: {sent} delivered, {failed} failed ===")
        if CURATOR_GROUP_ID:
            await bot.send_message(
                chat_id=CURATOR_GROUP_ID,
                text=f"📨 Friday digest sent — <b>{sent} delivered</b>, {failed} failed.",
                parse_mode=ParseMode.HTML,
            )

    try:
        asyncio.run(_send_all())
    except Exception as e:
        logger.error(f"Friday digest job failed: {e}")
        async def _send_err():
            await _alert_curator_group(bot, "send_friday_digest", e)
        asyncio.run(_send_err())


def send_reminders() -> None:
    """
    Job 4 — runs every hour.
    Sends event reminders that are due and marks them sent.
    """
    if not TELEGRAM_BOT_TOKEN:
        return

    due = db.get_due_reminders()
    if not due:
        return

    logger.info(f"Sending {len(due)} reminder(s)")
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    async def _send() -> None:
        for reminder in due:
            try:
                user     = reminder.get("users") or {}
                event    = reminder.get("events") or {}
                chat_id  = user.get("telegram_id")
                name     = event.get("name", "an event")
                date_raw = event.get("date_raw") or "today"
                location = event.get("location_raw", "")
                ticket   = event.get("ticket_url", "")

                lines = [f"🔔 <b>Reminder: {name}</b>", f"📅 {date_raw}"]
                if location:
                    lines.append(f"📍 {location}")
                if ticket:
                    lines.append(f'🎟 <a href="{ticket}">Get tickets</a>')
                lines.append("\nHave a great time! 🎉")

                await bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                db.mark_reminder_sent(reminder["id"])
            except Exception as e:
                logger.warning(f"Failed to send reminder {reminder['id']}: {e}")

    asyncio.run(_send())


def cleanup_past_events() -> None:
    """
    Job 3 — runs daily at 8am Lagos time.
    Deactivates events whose resolved date has passed.
    """
    logger.info("=== Cleanup job: deactivating past events ===")
    try:
        count = db.deactivate_past_events()
        logger.info(f"=== Cleanup complete: {count} event(s) deactivated ===")
    except Exception as e:
        logger.error(f"Cleanup job failed: {e}")
        if TELEGRAM_BOT_TOKEN:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            async def _alert():
                await _alert_curator_group(bot, "cleanup_past_events", e)
            asyncio.run(_alert())
