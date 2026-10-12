from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

VIBE_EMOJI = {
    "chill": "😌",
    "social": "🎉",
    "professional": "💼",
    "party": "🥳",
    "active": "💪",
    "intimate": "🕯️",
}

CATEGORY_EMOJI = {
    "music": "🎵",
    "comedy": "😂",
    "tech": "💻",
    "fitness": "🏃",
    "food": "🍽️",
    "art": "🎨",
    "networking": "🤝",
    "gaming": "🎮",
    "popup": "✨",
    "other": "📍",
}


def feedback_keyboard(
    event_id: str,
    existing_rating: int | None = None,
    reminder_set: bool = False,
) -> InlineKeyboardMarkup:
    """
    👍 / 👎 / 🔔 buttons for an event card.
    existing_rating: 1 = liked, -1 = disliked, None = no vote.
    reminder_set: True = reminder already scheduled (show ✅ 🔔).
    """
    short = event_id[:8]
    like_label   = "✅ 👍" if existing_rating == 1  else "👍"
    pass_label   = "✅ 👎" if existing_rating == -1 else "👎"
    remind_label = "✅ 🔔" if reminder_set          else "🔔"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(like_label,   callback_data=f"fb:1:{short}"),
        InlineKeyboardButton(pass_label,   callback_data=f"fb:0:{short}"),
        InlineKeyboardButton(remind_label, callback_data=f"rm:{short}"),
    ]])


def format_event(event: dict, index: int | None = None) -> str:
    """
    Format a single stored event dict into a Telegram message.
    Uses HTML formatting (Telegram's preferred mode).
    """
    parts: list[str] = []

    # Header
    prefix = f"{index}. " if index is not None else ""
    categories = event.get("categories", [])
    cat_emoji = CATEGORY_EMOJI.get(categories[0], "📍") if categories else "📍"
    parts.append(f"{cat_emoji} <b>{prefix}{event.get('name', 'Event')}</b>")

    # Date — show resolved date if we have it, otherwise the raw string
    date_iso = event.get("date")
    date_raw = event.get("date_raw")
    if date_iso:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
            date_display = dt.strftime("%-d %B %Y, %-I:%M %p")
        except Exception:
            date_display = date_raw or date_iso
        parts.append(f"📅 {date_display}")
    elif date_raw:
        parts.append(f"📅 {date_raw}")

    # Location
    location = event.get("location_raw")
    if location:
        parts.append(f"📍 {location}")

    # Cost
    if event.get("is_free"):
        parts.append("💚 Free entry")
    elif event.get("cost_raw"):
        parts.append(f"💰 {event['cost_raw']}")

    # Vibe
    vibe = event.get("vibe")
    if vibe:
        emoji = VIBE_EMOJI.get(vibe, "")
        parts.append(f"{emoji} {vibe.capitalize()} vibe")

    # Description
    desc = event.get("description")
    if desc:
        parts.append(f"\n{desc}")

    # Scarcity badge
    if event.get("is_scarce"):
        parts.append("🔥 <b>Limited spots — moving fast!</b>")

    # Links — ticket link takes priority over generic post link
    ticket_url = event.get("ticket_url")
    source_url = event.get("source_url")
    if ticket_url:
        parts.append(f'\n🎟 <a href="{ticket_url}">Get tickets / Book now</a>')
        if source_url:
            parts.append(f'<a href="{source_url}">View post</a>')
    elif source_url:
        parts.append(f'\n<a href="{source_url}">View post</a>')

    return "\n".join(parts)


def format_digest(events: list[dict]) -> str:
    """
    Format a list of events into the Friday digest message.
    """
    if not events:
        return (
            "😴 Nothing new this weekend — check back next Friday!\n\n"
            "Send me a spot or tip and I'll add it to the list."
        )

    lines = ["🌆 <b>What's happening around you this weekend</b>\n"]
    for i, event in enumerate(events, 1):
        lines.append(format_event(event, index=i))
        lines.append("")  # blank line between events

    lines.append("─────────────────")
    lines.append("React with 👍 or 👎 to help me learn what you like.")

    return "\n".join(lines)


def format_review_prompt(event: dict) -> str:
    """
    Format an event for the curator/admin review queue.
    Includes the event ID for approve/reject commands.
    """
    event_id = event.get("id", "")
    confidence = event.get("confidence", 0)

    text = format_event(event)
    text += f"\n\n<code>ID: {event_id}</code>"
    text += f"\n<i>Confidence: {confidence:.0%}</i>"
    text += f"\n\n✅ /approve_{event_id[:8]}\n❌ /reject_{event_id[:8]}"

    return text
