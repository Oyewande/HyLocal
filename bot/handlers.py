from __future__ import annotations

import os

from loguru import logger
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.formatter import format_digest, format_review_prompt
from processor.extractor import Extractor
from processor.geocoder import Geocoder
from scraper.models import RawSignal, SourcePlatform
from storage import db

CURATOR_GROUP_ID = int(os.getenv("TELEGRAM_CURATOR_GROUP_ID", "0"))

# Module-level singletons — instantiated once, reused on every message
_extractor: Extractor | None = None
_geocoder: Geocoder | None = None


def get_extractor() -> Extractor:
    global _extractor
    if _extractor is None:
        _extractor = Extractor()
    return _extractor


def get_geocoder() -> Geocoder:
    global _geocoder
    if _geocoder is None:
        _geocoder = Geocoder()
    return _geocoder


# ─────────────────────────────────────────────
# User-facing handlers (DMs)
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.get_or_create_user(
        telegram_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    await update.message.reply_text(
        f"Hey {user.first_name} 👋\n\n"
        "I find interesting things happening near you in Ikeja, Ketu, Magodo, Berger and surrounding areas.\n\n"
        "Every Friday I'll send you a curated weekend digest. You can also:\n"
        "• /weekend — see what's on right now\n"
        "• /prefs — set your interests and budget\n\n"
        "What kind of things do you enjoy? (music, tech, food, comedy, fitness...)",
        parse_mode=ParseMode.HTML,
    )


async def cmd_weekend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    events = db.get_approved_events(limit=7)
    digest = format_digest(events)
    await update.message.reply_text(digest, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_prefs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚙️ <b>Preferences</b>\n\n"
        "Tell me what you're into and I'll filter picks for you.\n\n"
        "Send me a message like:\n"
        "<i>interests: music, comedy, food\nbudget: 5000\nareas: Ikeja, Ketu</i>",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# Curator group handlers
# ─────────────────────────────────────────────

async def handle_curator_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Any message in the curator group is treated as a raw signal.
    Runs extraction and queues the result for review.
    """
    message = update.message
    if not message:
        return

    # Build a raw signal from whatever was sent
    raw_text = message.text or message.caption or ""
    image_urls: list[str] = []

    if message.photo:
        # Telegram gives multiple sizes — take the largest
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_urls.append(file.file_path)

    signal = RawSignal(
        source_platform=SourcePlatform.CURATOR,
        source_url=message.link if message.forward_from_chat else None,
        raw_text=raw_text,
        image_urls=image_urls,
        metadata={"submitted_by": message.from_user.username or str(message.from_user.id)},
    )

    try:
        event = get_extractor().extract(signal)

        if event is None:
            await message.reply_text("🤔 Couldn't find event details here. Try forwarding the original post.")
            return

        if event.location_raw:
            coords = get_geocoder().geocode(event.location_raw)
            if coords:
                event.location_lat, event.location_lng = coords

        stored = db.upsert_event(event)
        if stored:
            await message.reply_text(
                f"✅ Got it — <b>{event.name}</b> added to the review queue.\n"
                f"Confidence: {event.confidence:.0%}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply_text("⚠️ Event may already exist or couldn't be saved.")

    except Exception as e:
        logger.error(f"Curator submission failed: {e}")
        await message.reply_text("❌ Something went wrong processing this submission.")


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /approve_<id> commands from curators."""
    command = update.message.text or ""
    short_id = command.replace("/approve_", "").strip()

    # Find the full event ID from the short prefix
    pending = db.get_events_pending_review()
    match = next((e for e in pending if e["id"].startswith(short_id)), None)

    if not match:
        await update.message.reply_text("Event not found in review queue.")
        return

    if db.approve_event(match["id"]):
        await update.message.reply_text(f"✅ Approved: <b>{match['name']}</b>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Failed to approve.")


async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reject_<id> commands from curators."""
    command = update.message.text or ""
    short_id = command.replace("/reject_", "").strip()

    pending = db.get_events_pending_review()
    match = next((e for e in pending if e["id"].startswith(short_id)), None)

    if not match:
        await update.message.reply_text("Event not found in review queue.")
        return

    if db.reject_event(match["id"]):
        await update.message.reply_text(f"❌ Rejected: <b>{match['name']}</b>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Failed to reject.")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show pending review queue — curator group only."""
    pending = db.get_events_pending_review()
    if not pending:
        await update.message.reply_text("✅ Review queue is empty.")
        return

    total = len(pending)
    showing = min(5, total)
    header = f"📋 {total} event{'s' if total != 1 else ''} pending review — showing {showing}"
    await update.message.reply_text(header)
    for event in pending[:5]:  # show max 5 at a time
        text = format_review_prompt(event)
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ─────────────────────────────────────────────
# Router — decides which handler to use based on chat context
# ─────────────────────────────────────────────

def is_curator_group(update: Update) -> bool:
    if not update.effective_chat:
        return False
    return update.effective_chat.id == CURATOR_GROUP_ID


async def route_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route incoming messages to the right handler based on chat context."""
    if is_curator_group(update):
        await handle_curator_submission(update, context)
    # DMs with unrecognised text — gentle nudge
    elif update.message and update.message.text:
        await update.message.reply_text(
            "Try /weekend to see what's on, or /prefs to set your interests.",
            parse_mode=ParseMode.HTML,
        )


def register_handlers(app: Application) -> None:
    """Register all handlers on the bot application."""
    # User commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("weekend", cmd_weekend))
    app.add_handler(CommandHandler("prefs", cmd_prefs))

    # Curator-only commands (work anywhere but only make sense in the curator group)
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(MessageHandler(filters.Regex(r"^/approve_"), handle_approve))
    app.add_handler(MessageHandler(filters.Regex(r"^/reject_"), handle_reject))

    # Catch-all message router
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, route_message))
