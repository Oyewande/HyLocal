from __future__ import annotations

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

    # Date
    date_raw = event.get("date_raw")
    if date_raw:
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

    # Source link
    url = event.get("source_url")
    if url:
        parts.append(f'\n<a href="{url}">View post</a>')

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
