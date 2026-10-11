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

def upsert_event(event: ExtractedEvent) -> dict | None:
    """
    Insert an event, or skip if source_url already exists.
    Returns the stored row or None on failure.
    """
    db = get_client()

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


def get_approved_events(limit: int = 20) -> list[dict]:
    """Fetch approved upcoming events for the digest."""
    db = get_client()
    try:
        result = (
            db.table("events")
            .select("*")
            .eq("approved", True)
            .eq("active", True)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
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
# Feedback
# ─────────────────────────────────────────────

def record_feedback(user_id: str, event_id: str, rating: int) -> bool:
    """rating: 1 = like, -1 = dislike"""
    db = get_client()
    try:
        db.table("feedback").upsert(
            {"user_id": user_id, "event_id": event_id, "rating": rating},
            on_conflict="user_id,event_id",
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to record feedback: {e}")
        return False
