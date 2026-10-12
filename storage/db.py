from __future__ import annotations

import os
from typing import Any

from loguru import logger
from supabase import Client, create_client

from scraper.models import ExtractedEvent

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def get_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ─────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────

def find_duplicate_event(name: str, date_iso: str | None) -> dict | None:
    """
    Look for an existing event with a very similar name (case-insensitive)
    that falls on the same calendar day.  Used to prevent the same event
    appearing multiple times because it was scraped from Twitter, Instagram,
    *and* Eventbrite.

    Returns the existing row if a duplicate is found, otherwise None.
    """
    if not name:
        return None

    db = get_client()
    try:
        # Fetch recent active events and compare names in Python
        # (Supabase free tier doesn't expose pg_trgm similarity functions)
        result = (
            db.table("events")
            .select("id, name, date")
            .eq("active", True)
            .limit(200)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"Duplicate check query failed: {e}")
        return None

    name_normalised = _normalise(name)

    for row in rows:
        # Name similarity — simple token-overlap check
        if _name_similarity(name_normalised, _normalise(row.get("name", ""))) < 0.7:
            continue

        # If both have a date, they must be on the same calendar day
        if date_iso and row.get("date"):
            try:
                from datetime import datetime
                d1 = datetime.fromisoformat(date_iso.replace("Z", "+00:00")).date()
                d2 = datetime.fromisoformat(row["date"].replace("Z", "+00:00")).date()
                if d1 != d2:
                    continue
            except ValueError:
                pass  # unparseable date — fall through to name-only match

        logger.info(f"Duplicate detected: '{name}' matches existing event '{row['name']}' ({row['id'][:8]})")
        return row

    return None


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation and common filler words for comparison."""
    import re
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    stop = {"the", "a", "an", "at", "in", "on", "of", "and", "with", "ft", "featuring"}
    tokens = [t for t in text.split() if t not in stop]
    return " ".join(tokens)


def _name_similarity(a: str, b: str) -> float:
    """Token overlap: |intersection| / |union| (Jaccard similarity)."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def upsert_event(event: ExtractedEvent) -> dict | None:
    """
    Insert an event, or skip if source_url already exists *or* a cross-source
    duplicate is detected (same name + same date from a different platform).
    Returns the stored row or None on failure / duplicate.
    """
    db = get_client()

    # Cross-source duplicate check (skips events already in DB by name+date)
    duplicate = find_duplicate_event(
        event.name,
        event.date.isoformat() if event.date else None,
    )
    if duplicate:
        logger.info(f"Skipping duplicate event: '{event.name}'")
        return None

    row: dict[str, Any] = {
        "name": event.name,
        "description": event.description,
        "date_raw": event.date_raw,
        "date": event.date.isoformat() if event.date else None,
        "location_raw": event.location_raw,
        "cost_raw": event.cost_raw,
        "cost_min": event.cost_min,
        "cost_max": event.cost_max,
        "is_free": event.is_free,
        "categories": [c.value for c in event.categories],
        "vibe": event.vibe.value if event.vibe else None,
        "source_platform": event.source_platform.value,
        "source_url": event.source_url,
        "ticket_url": event.ticket_url,
        "is_scarce": event.is_scarce,
        "confidence": event.confidence,
        "needs_review": event.needs_review,
        "approved": not event.needs_review,  # Auto-approve high-confidence events
        "location_lat": event.location_lat,
        "location_lng": event.location_lng,
    }

    try:
        result = (
            db.table("events")
            .upsert(row, on_conflict="source_url", ignore_duplicates=True)
            .execute()
        )
        if result.data:
            logger.info(f"Stored event: '{event.name}'")
            return result.data[0]
        return None
    except Exception as e:
        logger.error(f"Failed to store event '{event.name}': {e}")
        return None


def get_approved_events(
    limit: int = 20,
    categories: list[str] | None = None,
    max_budget: int | None = None,
    neighbourhoods: list[str] | None = None,
    vibes: list[str] | None = None,
    is_free_only: bool = False,
    time_of_day: list[str] | None = None,
) -> list[dict]:
    """
    Fetch approved upcoming events for the digest.

    Ordering:
      - Events with a resolved date are sorted date ASC (soonest first)
      - Events without a date fall to the bottom, ordered by created_at DESC
      - Events whose date has already passed are excluded when date is known
    """
    from datetime import datetime, timezone

    db = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        query = (
            db.table("events")
            .select("*")
            .eq("approved", True)
            .eq("active", True)
        )

        # Exclude past events (only when date is set and in the past)
        # Supabase doesn't support OR across nullable columns cleanly,
        # so we fetch and filter in Python for simplicity.
        result = query.limit(limit * 3).execute()  # fetch extra to allow filtering
        rows = result.data or []

        # Filter out past events
        upcoming = []
        undated = []
        for row in rows:
            if row.get("date"):
                try:
                    event_dt = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
                    if event_dt >= datetime.now(timezone.utc):
                        upcoming.append(row)
                except ValueError:
                    undated.append(row)
            else:
                undated.append(row)

        # Apply optional filters
        _TIME_HOURS = {
            "morning":   range(6, 12),
            "afternoon": range(12, 17),
            "evening":   range(17, 21),
            "night":     range(21, 24),
        }

        def passes_filters(row: dict) -> bool:
            # Category / interest
            if categories and not any(c in (row.get("categories") or []) for c in categories):
                return False

            # Budget
            if max_budget is not None:
                cost = row.get("cost_min")
                if cost is not None and cost > max_budget:
                    return False

            # Free-only
            if is_free_only and not row.get("is_free"):
                return False

            # Neighbourhood / area
            if neighbourhoods:
                loc = (row.get("location_raw") or "").lower()
                if not any(n.lower() in loc for n in neighbourhoods):
                    return False

            # Vibe
            if vibes and row.get("vibe") not in vibes:
                return False

            # Time of day (requires resolved date)
            if time_of_day and row.get("date"):
                try:
                    dt = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
                    hour = dt.hour
                    allowed = any(hour in _TIME_HOURS.get(t, range(0)) for t in time_of_day)
                    if not allowed:
                        return False
                except ValueError:
                    pass  # date unparseable — don't filter out

            return True

        upcoming = [r for r in upcoming if passes_filters(r)]
        undated = [r for r in undated if passes_filters(r)]

        # Sort upcoming by date ASC, undated by created_at DESC
        upcoming.sort(key=lambda r: r["date"])
        undated.sort(key=lambda r: r.get("created_at", ""), reverse=True)

        combined = (upcoming + undated)[:limit]
        return combined

    except Exception as e:
        logger.error(f"Failed to fetch approved events: {e}")
        return []


def get_events_pending_review() -> list[dict]:
    """Fetch events waiting for manual review."""
    db = get_client()
    try:
        result = (
            db.table("events")
            .select("*")
            .eq("needs_review", True)
            .eq("approved", False)
            .order("created_at", desc=False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch pending events: {e}")
        return []


def approve_event(event_id: str) -> bool:
    db = get_client()
    try:
        db.table("events").update({"approved": True, "needs_review": False}).eq("id", event_id).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to approve event {event_id}: {e}")
        return False


def reject_event(event_id: str) -> bool:
    db = get_client()
    try:
        db.table("events").update({"active": False}).eq("id", event_id).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to reject event {event_id}: {e}")
        return False


# ─────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────

def get_or_create_user(telegram_id: int, username: str = "", first_name: str = "") -> dict | None:
    db = get_client()
    try:
        # Try to get existing user
        result = db.table("users").select("*").eq("telegram_id", telegram_id).execute()
        if result.data:
            return result.data[0]

        # Create new user
        result = db.table("users").insert({
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to get/create user {telegram_id}: {e}")
        return None


def get_all_active_users() -> list[dict]:
    db = get_client()
    try:
        result = db.table("users").select("*").eq("active", True).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        return []


# ─────────────────────────────────────────────
# User preferences
# ─────────────────────────────────────────────

def get_user_preferences(telegram_id: int) -> dict | None:
    """Return the preference row for a user, or None if not set."""
    client = get_client()
    try:
        user = client.table("users").select("id").eq("telegram_id", telegram_id).execute()
        if not user.data:
            return None
        user_id = user.data[0]["id"]
        result = client.table("preferences").select("*").eq("user_id", user_id).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to fetch prefs for {telegram_id}: {e}")
        return None


_UNSET = object()  # Sentinel: "caller didn't pass this arg"


def save_preferences(
    telegram_id: int,
    *,
    categories: list[str] | None = None,
    max_budget: Any = _UNSET,        # pass None explicitly to clear; omit to leave unchanged
    neighbourhoods: list[str] | None = None,
    vibes: list[str] | None = None,
    is_free_only: bool | None = None,
    time_of_day: list[str] | None = None,
) -> bool:
    """
    Upsert preferences for a user. Only keyword args that are not _UNSET are written —
    anything left unset keeps its existing DB value. Pass max_budget=None to clear it.
    """
    client = get_client()
    try:
        user = client.table("users").select("id").eq("telegram_id", telegram_id).execute()
        if not user.data:
            logger.warning(f"save_preferences: no user found for telegram_id {telegram_id}")
            return False
        user_id = user.data[0]["id"]

        row: dict[str, Any] = {"user_id": user_id}
        if categories is not None:
            row["categories"] = categories
        if max_budget is not _UNSET:
            row["max_budget"] = max_budget  # None → writes SQL NULL (clears the budget)
        if neighbourhoods is not None:
            row["neighbourhoods"] = neighbourhoods
        if vibes is not None:
            row["vibes"] = vibes
        if is_free_only is not None:
            row["is_free_only"] = is_free_only
        if time_of_day is not None:
            row["time_of_day"] = time_of_day

        client.table("preferences").upsert(row, on_conflict="user_id").execute()
        return True
    except Exception as e:
        logger.error(f"Failed to save prefs for {telegram_id}: {e}")
        return False


def search_events(query: str, limit: int = 10) -> list[dict]:
    """Full-text search across name, description, and location_raw."""
    client = get_client()
    q = query.strip()
    if not q:
        return []
    try:
        result = (
            client.table("events")
            .select("*")
            .eq("approved", True)
            .eq("active", True)
            .or_(f"name.ilike.%{q}%,description.ilike.%{q}%,location_raw.ilike.%{q}%")
            .order("date", desc=False, nullsfirst=False)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Search failed for '{q}': {e}")
        return []


def get_event_by_short_id(short_id: str) -> dict | None:
    """Look up an event by the first 8 chars of its UUID."""
    client = get_client()
    try:
        result = client.table("events").select("*").like("id", f"{short_id}%").limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to look up event by short id '{short_id}': {e}")
        return None


def update_event_field(event_id: str, field: str, value: Any) -> bool:
    """Update a single field on an event row."""
    client = get_client()
    try:
        client.table("events").update({field: value}).eq("id", event_id).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to update event {event_id} field '{field}': {e}")
        return False


def deactivate_past_events() -> int:
    """
    Mark events with a known past date as inactive.
    Returns the number of events deactivated.
    """
    from datetime import datetime, timezone
    client = get_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        result = (
            client.table("events")
            .update({"active": False})
            .eq("active", True)
            .lt("date", now_iso)
            .neq("date", None)
            .execute()
        )
        count = len(result.data) if result.data else 0
        if count:
            logger.info(f"Deactivated {count} past event(s)")
        return count
    except Exception as e:
        logger.error(f"Failed to deactivate past events: {e}")
        return 0


# ─────────────────────────────────────────────
# Feedback
# ─────────────────────────────────────────────

def record_feedback(user_id: str, event_id: str, rating: int) -> bool:
    """rating: 1 = like, -1 = dislike"""
    client = get_client()
    try:
        client.table("feedback").upsert(
            {"user_id": user_id, "event_id": event_id, "rating": rating},
            on_conflict="user_id,event_id",
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to record feedback: {e}")
        return False


def set_reminder(user_id: str, event_id: str, remind_at: str) -> bool:
    """Schedule a reminder for a user about an event. remind_at is an ISO datetime string."""
    client = get_client()
    try:
        client.table("reminders").upsert(
            {"user_id": user_id, "event_id": event_id, "remind_at": remind_at, "sent": False},
            on_conflict="user_id,event_id",
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to set reminder: {e}")
        return False


def get_due_reminders() -> list[dict]:
    """Return unsent reminders whose remind_at is in the past or within the next hour."""
    from datetime import datetime, timedelta, timezone
    client = get_client()
    window = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    try:
        result = (
            client.table("reminders")
            .select("*, users(telegram_id, first_name), events(name, date_raw, date, location_raw, ticket_url)")
            .eq("sent", False)
            .lte("remind_at", window)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch due reminders: {e}")
        return []


def mark_reminder_sent(reminder_id: str) -> None:
    client = get_client()
    try:
        client.table("reminders").update({"sent": True}).eq("id", reminder_id).execute()
    except Exception as e:
        logger.error(f"Failed to mark reminder {reminder_id} sent: {e}")


def get_user_feedback(user_id: str, event_id: str) -> int | None:
    """Return the user's existing rating (1 or -1) for an event, or None."""
    client = get_client()
    try:
        result = (
            client.table("feedback")
            .select("rating")
            .eq("user_id", user_id)
            .eq("event_id", event_id)
            .execute()
        )
        return result.data[0]["rating"] if result.data else None
    except Exception as e:
        logger.error(f"Failed to get feedback: {e}")
        return None
