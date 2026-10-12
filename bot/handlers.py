from __future__ import annotations

import os
import re

import dateparser

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.formatter import feedback_keyboard, format_digest, format_event, format_review_prompt
from processor.extractor import Extractor
from processor.geocoder import Geocoder
from scraper.models import EventCategory, RawSignal, SourcePlatform, SocialVibe
from storage import db

CURATOR_GROUP_ID = int(os.getenv("TELEGRAM_CURATOR_GROUP_ID") or "0")

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
        "• /search — find events by keyword\n"
        "• /prefs — set your interests and budget\n\n"
        "What kind of things do you enjoy? (music, tech, food, comedy, fitness...)",
        parse_mode=ParseMode.HTML,
    )


async def _send_events_with_buttons(
    send_fn,
    events: list[dict],
    user_uuid: str | None = None,
) -> None:
    """
    Send each event as its own message with 👍/👎 buttons.
    send_fn is an async callable that accepts (text, parse_mode, reply_markup, disable_web_page_preview).
    user_uuid is the DB UUID (not Telegram ID) used to look up existing feedback.
    """
    for i, event in enumerate(events, 1):
        text = format_event(event, index=i)
        existing = db.get_user_feedback(user_uuid, event["id"]) if user_uuid else None
        kb = feedback_keyboard(event["id"], existing_rating=existing)
        await send_fn(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)


async def cmd_weekend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    prefs = db.get_user_preferences(user.id) or {}
    user_row = db.get_or_create_user(telegram_id=user.id, username=user.username or "", first_name=user.first_name or "")
    user_uuid = user_row["id"] if user_row else None

    events = db.get_approved_events(
        limit=7,
        categories=prefs.get("categories") or None,
        max_budget=prefs.get("max_budget"),
        neighbourhoods=prefs.get("neighbourhoods") or None,
        vibes=prefs.get("vibes") or None,
        is_free_only=bool(prefs.get("is_free_only", False)),
        time_of_day=prefs.get("time_of_day") or None,
    )

    has_prefs = any([
        prefs.get("categories"), prefs.get("max_budget"), prefs.get("neighbourhoods"),
        prefs.get("vibes"), prefs.get("is_free_only"), prefs.get("time_of_day"),
    ])

    if not events and has_prefs:
        events = db.get_approved_events(limit=7)
        await update.message.reply_text(
            "⚠️ Nothing matched your preferences — showing everything instead. Use /prefs to adjust.",
            parse_mode=ParseMode.HTML,
        )

    if not events:
        await update.message.reply_text(
            "😴 Nothing new this weekend — check back next Friday!\n\nSend a spot to the curator group and I'll add it.",
        )
        return

    await update.message.reply_text(
        "🌆 <b>What's happening around you this weekend</b>",
        parse_mode=ParseMode.HTML,
    )
    await _send_events_with_buttons(update.message.reply_text, events, user_uuid)


# ─────────────────────────────────────────────
# Preferences — inline keyboard UI
# ─────────────────────────────────────────────
#
# Callback data format (max 64 bytes):
#   p:menu          — main menu
#   p:s:cat         — open interests section
#   p:s:vib         — open vibe section
#   p:s:tim         — open time section
#   p:s:are         — open areas section
#   p:s:fre         — open free-only section
#   p:s:bud         — prompt budget input
#   p:t:cat:{val}   — toggle a category
#   p:t:vib:{val}   — toggle a vibe
#   p:t:tim:{val}   — toggle a time slot
#   p:t:are:{val}   — toggle a neighbourhood
#   p:t:fre:{0|1}   — set free-only
#   p:bud:clr       — clear budget

_AREAS = [
    "Ikeja", "Ketu", "Magodo", "Berger", "Yaba",
    "Lekki", "VI", "Ikoyi", "Surulere", "Maryland",
    "Gbagada", "Ajah", "Ojodu", "Oshodi", "Sangotedo",
]

_CAT_LABELS = {
    "music": "🎵 Music", "comedy": "😂 Comedy", "tech": "💻 Tech",
    "fitness": "🏃 Fitness", "food": "🍽️ Food", "art": "🎨 Art",
    "networking": "🤝 Networking", "gaming": "🎮 Gaming", "popup": "✨ Pop-up",
}
_VIBE_LABELS = {
    "chill": "😌 Chill", "social": "🎉 Social", "professional": "💼 Professional",
    "party": "🥳 Party", "active": "💪 Active", "intimate": "🕯️ Intimate",
}
_TIME_LABELS = {
    "morning": "🌅 Morning (6–12)",
    "afternoon": "☀️ Afternoon (12–5)",
    "evening": "🌆 Evening (5–9)",
    "night": "🌙 Night (9pm+)",
}


def _prefs_summary(prefs: dict) -> str:
    lines: list[str] = []
    if prefs.get("categories"):
        labels = [_CAT_LABELS.get(c, c).split(" ", 1)[-1] for c in prefs["categories"]]
        lines.append(f"🎯 <b>Interests:</b> {', '.join(labels)}")
    if prefs.get("max_budget"):
        lines.append(f"💰 <b>Budget:</b> up to ₦{prefs['max_budget']:,}")
    if prefs.get("is_free_only"):
        lines.append("💚 <b>Free events only</b>")
    if prefs.get("neighbourhoods"):
        lines.append(f"📍 <b>Areas:</b> {', '.join(prefs['neighbourhoods'])}")
    if prefs.get("vibes"):
        labels = [_VIBE_LABELS.get(v, v).split(" ", 1)[-1] for v in prefs["vibes"]]
        lines.append(f"✨ <b>Vibe:</b> {', '.join(labels)}")
    if prefs.get("time_of_day"):
        labels = [t.capitalize() for t in prefs["time_of_day"]]
        lines.append(f"🕐 <b>Time:</b> {', '.join(labels)}")
    return "\n".join(lines) if lines else "<i>No preferences set yet.</i>"


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 Interests", callback_data="p:s:cat"),
            InlineKeyboardButton("✨ Vibe",      callback_data="p:s:vib"),
        ],
        [
            InlineKeyboardButton("🕐 Time of day", callback_data="p:s:tim"),
            InlineKeyboardButton("📍 Areas",        callback_data="p:s:are"),
        ],
        [
            InlineKeyboardButton("💚 Free only", callback_data="p:s:fre"),
            InlineKeyboardButton("💰 Budget",    callback_data="p:s:bud"),
        ],
        [InlineKeyboardButton("🗓 See this weekend's events →", callback_data="p:weekend")],
    ])


def _toggle_keyboard(
    section: str,
    options: dict[str, str],   # value → label
    selected: list[str],
) -> InlineKeyboardMarkup:
    """Build a 2-column toggle keyboard. ✅ prefix = selected."""
    rows: list[list[InlineKeyboardButton]] = []
    items = list(options.items())
    for i in range(0, len(items), 2):
        row = []
        for value, label in items[i:i+2]:
            tick = "✅ " if value in selected else ""
            row.append(InlineKeyboardButton(
                f"{tick}{label}",
                callback_data=f"p:t:{section}:{value}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("✅ Done", callback_data="p:menu")])
    return InlineKeyboardMarkup(rows)


def _area_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(_AREAS), 3):
        row = []
        for area in _AREAS[i:i+3]:
            tick = "✅ " if area in selected else ""
            row.append(InlineKeyboardButton(
                f"{tick}{area}",
                callback_data=f"p:t:are:{area}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("✅ Done", callback_data="p:menu")])
    return InlineKeyboardMarkup(rows)


def _free_keyboard(is_free_only: bool) -> InlineKeyboardMarkup:
    yes_tick = "✅ " if is_free_only else ""
    no_tick  = "✅ " if not is_free_only else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{yes_tick}Free events only", callback_data="p:t:fre:1"),
            InlineKeyboardButton(f"{no_tick}All events",        callback_data="p:t:fre:0"),
        ],
        [InlineKeyboardButton("✅ Done", callback_data="p:menu")],
    ])


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🗺 <b>HyLocal — command guide</b>\n\n"
        "<b>Discover events</b>\n"
        "/weekend — see what's on this weekend\n"
        "/search &lt;keyword&gt; — find events by name, venue or vibe\n\n"
        "<b>Personalise</b>\n"
        "/prefs — set your interests, budget, areas &amp; more\n\n"
        "<b>Flyers</b>\n"
        "Send any flyer image directly to this chat — I'll read it and show you the event details.\n\n"
        "<b>Feedback</b>\n"
        "Tap 👍 or 👎 on any event to help me learn what you like.\n"
        "Tap 🔔 to set a reminder for the morning of the event.\n\n"
        "Questions? Ping the team in the HyLocal group.",
        parse_mode="HTML",
    )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args or []).strip()
    if not query:
        await update.message.reply_text(
            "Send a keyword to search — e.g. <code>/search afrobeats</code> or <code>/search Ikeja</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    user = update.effective_user
    user_row = db.get_or_create_user(telegram_id=user.id, username=user.username or "", first_name=user.first_name or "")
    user_uuid = user_row["id"] if user_row else None

    events = db.search_events(query, limit=5)
    if not events:
        await update.message.reply_text(f"🔍 No events found for <b>{query}</b>. Try a different keyword.", parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text(
        f"🔍 <b>{len(events)} result{'s' if len(events) != 1 else ''} for "{query}"</b>",
        parse_mode=ParseMode.HTML,
    )
    await _send_events_with_buttons(update.message.reply_text, events, user_uuid)


async def cmd_prefs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    prefs = db.get_user_preferences(user.id) or {}
    text = "⚙️ <b>Your Preferences</b>\n\n" + _prefs_summary(prefs) + "\n\nWhat would you like to change?"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_main_menu_keyboard())


async def handle_prefs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all p:* callback queries from the prefs inline keyboard."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    data = query.data or ""

    if not data.startswith("p:"):
        return

    # Ensure the user record exists — they may not have sent /start yet
    db.get_or_create_user(
        telegram_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    parts = data.split(":")  # e.g. ["p", "s", "cat"] or ["p", "t", "cat", "music"]
    action = parts[1] if len(parts) > 1 else ""

    prefs = db.get_user_preferences(user.id) or {}

    # ── Weekend shortcut ───────────────────────────────────────────────────
    if action == "weekend":
        events = db.get_approved_events(
            limit=7,
            categories=prefs.get("categories") or None,
            max_budget=prefs.get("max_budget"),
            neighbourhoods=prefs.get("neighbourhoods") or None,
            vibes=prefs.get("vibes") or None,
            is_free_only=bool(prefs.get("is_free_only", False)),
            time_of_day=prefs.get("time_of_day") or None,
        )
        if not events:
            events = db.get_approved_events(limit=7)
            digest = "⚠️ Nothing matched your preferences — showing everything.\n\n" + format_digest(events)
        else:
            digest = format_digest(events)
        # Send as a new message so the prefs card stays intact
        await query.message.reply_text(digest, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    # ── Main menu ──────────────────────────────────────────────────────────
    if action == "menu":
        # Show a brief "Saved" toast (appears as a notification above the keyboard for ~2s)
        await query.answer("✅ Saved!", show_alert=False)
        text = "⚙️ <b>Your Preferences</b>\n\n" + _prefs_summary(prefs) + "\n\nWhat else would you like to change?"
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=_main_menu_keyboard())
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
        return

    # ── Open a section ─────────────────────────────────────────────────────
    if action == "s":
        section = parts[2] if len(parts) > 2 else ""

        _hint = "<i>Tap to select · tap again to remove · tap ✅ Done when finished</i>"

        if section == "cat":
            kb = _toggle_keyboard("cat", _CAT_LABELS, prefs.get("categories") or [])
            await query.edit_message_text(f"🎯 <b>Interests</b>\n{_hint}", parse_mode=ParseMode.HTML, reply_markup=kb)

        elif section == "vib":
            kb = _toggle_keyboard("vib", _VIBE_LABELS, prefs.get("vibes") or [])
            await query.edit_message_text(f"✨ <b>Vibe</b>\n{_hint}", parse_mode=ParseMode.HTML, reply_markup=kb)

        elif section == "tim":
            kb = _toggle_keyboard("tim", _TIME_LABELS, prefs.get("time_of_day") or [])
            await query.edit_message_text(f"🕐 <b>Time of day</b>\n{_hint}", parse_mode=ParseMode.HTML, reply_markup=kb)

        elif section == "are":
            kb = _area_keyboard(prefs.get("neighbourhoods") or [])
            await query.edit_message_text(f"📍 <b>Areas</b>\n{_hint}", parse_mode=ParseMode.HTML, reply_markup=kb)

        elif section == "fre":
            kb = _free_keyboard(bool(prefs.get("is_free_only", False)))
            await query.edit_message_text(f"💚 <b>Free events only?</b>\n{_hint}", parse_mode=ParseMode.HTML, reply_markup=kb)

        elif section == "bud":
            context.user_data["awaiting_budget"] = True
            budget = prefs.get("max_budget")
            current = f"Current: ₦{budget:,}" if budget else "Not set"
            await query.edit_message_text(
                f"💰 <b>Budget</b>\n{current}\n\nSend me your max spend per event in ₦ (e.g. <code>5000</code>).\nSend <code>0</code> to clear it.",
                parse_mode=ParseMode.HTML,
            )
        return

    # ── Toggle a value ─────────────────────────────────────────────────────
    if action == "t":
        section = parts[2] if len(parts) > 2 else ""
        value   = parts[3] if len(parts) > 3 else ""

        async def _safe_update_kb(kb: InlineKeyboardMarkup) -> None:
            try:
                await query.edit_message_reply_markup(reply_markup=kb)
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    pass  # already showing correct state — safe to ignore
                else:
                    raise

        if section == "cat":
            current: list[str] = list(prefs.get("categories") or [])
            current = [v for v in current if v != value] if value in current else current + [value]
            db.save_preferences(user.id, categories=current)
            prefs = db.get_user_preferences(user.id) or {}
            await _safe_update_kb(_toggle_keyboard("cat", _CAT_LABELS, prefs.get("categories") or []))

        elif section == "vib":
            current = list(prefs.get("vibes") or [])
            current = [v for v in current if v != value] if value in current else current + [value]
            db.save_preferences(user.id, vibes=current)
            prefs = db.get_user_preferences(user.id) or {}
            await _safe_update_kb(_toggle_keyboard("vib", _VIBE_LABELS, prefs.get("vibes") or []))

        elif section == "tim":
            current = list(prefs.get("time_of_day") or [])
            current = [v for v in current if v != value] if value in current else current + [value]
            db.save_preferences(user.id, time_of_day=current)
            prefs = db.get_user_preferences(user.id) or {}
            await _safe_update_kb(_toggle_keyboard("tim", _TIME_LABELS, prefs.get("time_of_day") or []))

        elif section == "are":
            current = list(prefs.get("neighbourhoods") or [])
            current = [v for v in current if v != value] if value in current else current + [value]
            db.save_preferences(user.id, neighbourhoods=current)
            prefs = db.get_user_preferences(user.id) or {}
            await _safe_update_kb(_area_keyboard(prefs.get("neighbourhoods") or []))

        elif section == "fre":
            is_free = value == "1"
            db.save_preferences(user.id, is_free_only=is_free)
            await _safe_update_kb(_free_keyboard(is_free))

        return


# ─────────────────────────────────────────────
# Feedback handler
# ─────────────────────────────────────────────

async def handle_feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 👍/👎 taps on event cards. Callback data: fb:{1|0}:{short_id}"""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")  # ["fb", "1", "abc12345"]
    if len(parts) < 3:
        return

    is_like  = parts[1] == "1"
    short_id = parts[2]
    rating   = 1 if is_like else -1

    tg_user  = update.effective_user
    user_row = db.get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
    )
    event = db.get_event_by_short_id(short_id)

    if not event or not user_row:
        await query.answer("Couldn't find that event.", show_alert=False)
        return

    db.record_feedback(user_row["id"], event["id"], rating)

    toast = "👍 Noted — more like this!" if is_like else "👎 Got it — fewer of these."
    await query.answer(toast, show_alert=False)

    # Update buttons to reflect the selection
    kb = feedback_keyboard(event["id"], existing_rating=rating)
    try:
        await query.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ─────────────────────────────────────────────
# Reminder handler
# ─────────────────────────────────────────────

async def handle_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 🔔 Remind me taps. Callback data: rm:{short_id}"""
    query = update.callback_query
    await query.answer()

    short_id = query.data.split(":")[-1]
    tg_user  = update.effective_user
    user_row = db.get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
    )
    event = db.get_event_by_short_id(short_id)

    if not event or not user_row:
        await query.answer("Couldn't find that event.", show_alert=False)
        return

    if not event.get("date"):
        await query.answer(
            "No confirmed date yet — I'll remind you once a date is set.",
            show_alert=True,
        )
        return

    from datetime import datetime, timezone, timedelta
    event_dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
    # Remind at 9am Lagos (UTC+1) on the day of the event
    remind_at = event_dt.replace(hour=8, minute=0, second=0, microsecond=0)  # 8am UTC = 9am Lagos
    if remind_at < datetime.now(timezone.utc):
        remind_at = datetime.now(timezone.utc) + timedelta(minutes=30)  # event today — remind in 30 min

    ok = db.set_reminder(user_row["id"], event["id"], remind_at.isoformat())
    if ok:
        date_str = event_dt.strftime("%-d %B")
        await query.answer(f"🔔 I'll remind you the morning of {date_str}!", show_alert=True)
        kb = feedback_keyboard(event["id"], reminder_set=True)
        try:
            await query.edit_message_reply_markup(reply_markup=kb)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
    else:
        await query.answer("Couldn't set reminder — try again.", show_alert=True)


# ─────────────────────────────────────────────
# Curator group handlers
# ─────────────────────────────────────────────

async def _download_message_images(message, context) -> list[bytes]:
    """
    Download all images from a Telegram message.
    Handles both compressed photos (message.photo) and
    image documents (message.document with image mime type).
    Returns a list of raw image bytes (largest photo only for photos).
    """
    images: list[bytes] = []

    # Compressed photos — take the largest size
    if message.photo:
        try:
            file = await context.bot.get_file(message.photo[-1].file_id)
            data = bytes(await file.download_as_bytearray())
            images.append(data)
        except Exception as e:
            logger.warning(f"Failed to download photo: {e}")

    # Uncompressed image document (e.g. JPG/PNG sent as file)
    if message.document and message.document.mime_type and \
            message.document.mime_type.startswith("image/"):
        try:
            file = await context.bot.get_file(message.document.file_id)
            data = bytes(await file.download_as_bytearray())
            images.append(data)
        except Exception as e:
            logger.warning(f"Failed to download image document: {e}")

    return images


async def _extract_and_reply(message, context, platform: SourcePlatform, is_curator: bool = False) -> None:
    """
    Shared extraction flow for both curator group and regular user photo submissions.
    Downloads images, runs extraction, stores result (curator) or replies with event
    details (regular user).
    """
    raw_text = message.text or message.caption or ""
    images   = await _download_message_images(message, context)

    if not raw_text and not images:
        await message.reply_text("🤔 Send a flyer image, a text post, or both.")
        return

    signal = RawSignal(
        source_platform=platform,
        source_url=message.link if hasattr(message, "link") and message.forward_origin else None,
        raw_text=raw_text or None,
        image_urls=[],  # bytes passed via preloaded_images instead
        metadata={"submitted_by": message.from_user.username or str(message.from_user.id)},
    )

    await message.reply_text("🔍 Reading the flyer…" if images else "🔍 Processing…")

    try:
        event = get_extractor().extract(signal, preloaded_images=images or None)

        if event is None:
            await message.reply_text(
                "🤔 Couldn't spot an event in there. If this is a flyer, try sending it without compression "
                "(Telegram → attach → File instead of Photo) so the text stays sharp."
            )
            return

        # Resolve date
        if event.date_raw and event.date is None:
            parsed = dateparser.parse(
                event.date_raw,
                settings={"TIMEZONE": "Africa/Lagos", "RETURN_AS_TIMEZONE_AWARE": True, "PREFER_DATES_FROM": "future"},
            )
            if parsed:
                event.date = parsed

        if event.location_raw:
            coords = get_geocoder().geocode(event.location_raw)
            if coords:
                event.location_lat, event.location_lng = coords

        if is_curator:
            # Store and ask for approve/reject
            stored = db.upsert_event(event)
            if stored:
                preview = format_event(stored)
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"cur:approve:{stored['id'][:8]}"),
                    InlineKeyboardButton("❌ Reject",  callback_data=f"cur:reject:{stored['id'][:8]}"),
                ]])
                await message.reply_text(
                    f"📋 <b>Extracted — does this look right?</b>\n\n{preview}\n\n"
                    f"<i>Confidence: {event.confidence:.0%}</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
            else:
                await message.reply_text("⚠️ Event may already exist or couldn't be saved.")
        else:
            # Regular user — just show the extracted details (don't store)
            preview = format_event(event)
            await message.reply_text(
                f"📋 <b>Here's what I found in that flyer:</b>\n\n{preview}",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    except Exception as e:
        logger.error(f"Extraction from {'curator' if is_curator else 'user'} submission failed: {e}")
        await message.reply_text("❌ Something went wrong. Try again or add a caption with the event details.")


async def handle_curator_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Any message in the curator group is treated as a raw signal.
    Handles text posts, forwarded posts, flyer photos, and image documents.
    """
    if not update.message:
        return
    await _extract_and_reply(update.message, context, SourcePlatform.CURATOR, is_curator=True)


async def handle_curator_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ✅ Approve / ❌ Reject inline buttons in the curator group."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")  # ["cur", "approve"|"reject", short_id]
    if len(parts) < 3:
        return

    action   = parts[1]
    short_id = parts[2]

    event = db.get_event_by_short_id(short_id)
    if not event:
        await query.answer("Event not found.", show_alert=True)
        return

    if action == "approve":
        db.approve_event(event["id"])
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"✅ <b>{event['name']}</b> approved and live!", parse_mode=ParseMode.HTML)
    elif action == "reject":
        db.reject_event(event["id"])
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"❌ <b>{event['name']}</b> rejected.", parse_mode=ParseMode.HTML)


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


_EDITABLE_FIELDS = {"name", "description", "date_raw", "location_raw", "cost_raw", "ticket_url"}


async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Curator command to fix AI extraction errors.
    Format: /edit_<short_id> field: new value
    Example: /edit_abc12345 name: Afrobeats Night at Balmoral
    """
    text = update.message.text or ""
    match = re.match(r"^/edit_(\w+)\s+(\w+):\s*(.+)$", text.strip(), re.DOTALL)

    if not match:
        await update.message.reply_text(
            "Format: <code>/edit_&lt;id&gt; field: new value</code>\n"
            f"Editable fields: {', '.join(sorted(_EDITABLE_FIELDS))}",
            parse_mode=ParseMode.HTML,
        )
        return

    short_id = match.group(1)
    field    = match.group(2).lower()
    value    = match.group(3).strip()

    if field not in _EDITABLE_FIELDS:
        await update.message.reply_text(
            f"❌ Can't edit <code>{field}</code>.\n"
            f"Editable fields: {', '.join(sorted(_EDITABLE_FIELDS))}",
            parse_mode=ParseMode.HTML,
        )
        return

    event = db.get_event_by_short_id(short_id)
    if not event:
        await update.message.reply_text(f"Event <code>{short_id}</code> not found.", parse_mode=ParseMode.HTML)
        return

    if db.update_event_field(event["id"], field, value):
        await update.message.reply_text(
            f"✅ <b>{event['name']}</b> updated\n"
            f"<code>{field}</code>: {value}",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("❌ Update failed.")


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
        return

    msg = update.message
    if not msg:
        return

    # Photo or image document sent by a regular user — extract and show flyer details
    has_photo = bool(msg.photo)
    has_image_doc = bool(
        msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/")
    )
    if has_photo or has_image_doc:
        await _extract_and_reply(msg, context, SourcePlatform.MANUAL, is_curator=False)
        return

    if not msg.text:
        return

    text = msg.text.strip()
    user = update.effective_user

    # Budget input — triggered when user tapped 💰 Budget in the prefs menu
    if context.user_data.get("awaiting_budget"):
        context.user_data.pop("awaiting_budget")
        numeric = re.sub(r"[^\d]", "", text)
        if not numeric:
            await update.message.reply_text("That doesn't look like a number. Send a number like <code>5000</code> or <code>0</code> to clear.", parse_mode=ParseMode.HTML)
            return
        amount = int(numeric)
        if amount == 0:
            db.save_preferences(user.id, max_budget=None)
            await update.message.reply_text("💰 Budget cleared. Use /prefs to update your preferences.")
        else:
            db.save_preferences(user.id, max_budget=amount)
            await update.message.reply_text(
                f"💰 Budget set to ₦{amount:,}. Use /prefs to see all your preferences.",
                parse_mode=ParseMode.HTML,
            )
        return

    # Default nudge
    await update.message.reply_text(
        "Try /weekend to see what's on, or /prefs to set your interests.",
    )


def register_handlers(app: Application) -> None:
    """Register all handlers on the bot application."""
    # User commands
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("weekend", cmd_weekend))
    app.add_handler(CommandHandler("search",  cmd_search))
    app.add_handler(CommandHandler("prefs",   cmd_prefs))
    app.add_handler(CommandHandler("help",    cmd_help))

    # Inline keyboard callbacks for prefs
    app.add_handler(CallbackQueryHandler(handle_prefs_callback, pattern=r"^p:"))

    # Feedback buttons (👍/👎 on event cards)
    app.add_handler(CallbackQueryHandler(handle_feedback_callback, pattern=r"^fb:"))
    # Reminder button (🔔 on event cards)
    app.add_handler(CallbackQueryHandler(handle_reminder_callback, pattern=r"^rm:"))
    # Curator approve/reject inline buttons
    app.add_handler(CallbackQueryHandler(handle_curator_inline, pattern=r"^cur:"))

    # Curator-only commands (work anywhere but only make sense in the curator group)
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(MessageHandler(filters.Regex(r"^/approve_"), handle_approve))
    app.add_handler(MessageHandler(filters.Regex(r"^/reject_"), handle_reject))
    app.add_handler(MessageHandler(filters.Regex(r"^/edit_"), handle_edit))

    # Catch-all message router
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, route_message))
