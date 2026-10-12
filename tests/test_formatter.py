"""
Tests for bot/formatter.py

Checks that format_event produces correct HTML for various event shapes,
and that feedback_keyboard buttons carry the right callback data.
"""
from __future__ import annotations

import pytest
from bot.formatter import format_event, feedback_keyboard


def event(**kwargs) -> dict:
    base = {
        "id": "abcdef12-0000-0000-0000-000000000000",
        "name": "Test Event",
        "categories": [],
        "is_free": False,
        "is_scarce": False,
        "ticket_url": None,
        "source_url": None,
        "date": None,
        "date_raw": None,
        "location_raw": None,
        "cost_raw": None,
        "vibe": None,
        "description": None,
    }
    base.update(kwargs)
    return base


# ── format_event ──────────────────────────────────────────────────────────────

class TestFormatEvent:
    def test_name_appears(self):
        text = format_event(event(name="Jazz Night"))
        assert "Jazz Night" in text

    def test_index_prefix(self):
        text = format_event(event(name="Jazz Night"), index=3)
        assert "3." in text

    def test_free_entry_label(self):
        text = format_event(event(is_free=True))
        assert "Free" in text

    def test_paid_cost_appears(self):
        text = format_event(event(cost_raw="₦5,000"))
        assert "₦5,000" in text

    def test_location_appears(self):
        text = format_event(event(location_raw="Terra Kulture, VI"))
        assert "Terra Kulture, VI" in text

    def test_date_raw_fallback(self):
        text = format_event(event(date_raw="this Saturday"))
        assert "this Saturday" in text

    def test_scarcity_badge(self):
        text = format_event(event(is_scarce=True))
        assert "Limited spots" in text

    def test_no_scarcity_badge_when_false(self):
        text = format_event(event(is_scarce=False))
        assert "Limited spots" not in text

    def test_ticket_url_link(self):
        text = format_event(event(ticket_url="https://paystack.com/buy/event"))
        assert "https://paystack.com/buy/event" in text
        assert "Get tickets" in text

    def test_source_url_link(self):
        text = format_event(event(source_url="https://instagram.com/p/abc"))
        assert "https://instagram.com/p/abc" in text

    def test_ticket_url_takes_priority_over_source(self):
        text = format_event(event(
            ticket_url="https://paystack.com/buy/event",
            source_url="https://instagram.com/p/abc",
        ))
        # Both should appear, but ticket link should come first
        ticket_pos = text.find("paystack.com")
        source_pos = text.find("instagram.com")
        assert ticket_pos < source_pos

    def test_category_emoji_music(self):
        text = format_event(event(categories=["music"]))
        assert "🎵" in text

    def test_category_emoji_tech(self):
        text = format_event(event(categories=["tech"]))
        assert "💻" in text

    def test_fallback_emoji_no_category(self):
        text = format_event(event(categories=[]))
        assert "📍" in text

    def test_description_appears(self):
        text = format_event(event(description="A great evening of live music."))
        assert "A great evening of live music." in text

    def test_resolved_date_displayed(self):
        # Provide an ISO date string — formatter should show it in readable form
        text = format_event(event(date="2026-10-17T20:00:00+01:00"))
        assert "2026" in text or "October" in text or "17" in text


# ── feedback_keyboard ─────────────────────────────────────────────────────────

class TestFeedbackKeyboard:
    def test_three_buttons(self):
        kb = feedback_keyboard("abcdef12-0000-0000-0000-000000000000")
        buttons = kb.inline_keyboard[0]
        assert len(buttons) == 3

    def test_callback_prefixes(self):
        kb = feedback_keyboard("abcdef12-0000-0000-0000-000000000000")
        data = [btn.callback_data for btn in kb.inline_keyboard[0]]
        assert any(d.startswith("fb:") for d in data)
        assert any(d.startswith("rm:") for d in data)

    def test_short_id_used(self):
        kb = feedback_keyboard("abcdef12-0000-0000-0000-000000000000")
        data = [btn.callback_data for btn in kb.inline_keyboard[0]]
        assert all("abcdef12" in d for d in data)

    def test_liked_state(self):
        kb = feedback_keyboard("abcdef12-0000-0000-0000-000000000000", existing_rating=1)
        labels = [btn.text for btn in kb.inline_keyboard[0]]
        assert "✅ 👍" in labels

    def test_disliked_state(self):
        kb = feedback_keyboard("abcdef12-0000-0000-0000-000000000000", existing_rating=-1)
        labels = [btn.text for btn in kb.inline_keyboard[0]]
        assert "✅ 👎" in labels

    def test_reminder_set_state(self):
        kb = feedback_keyboard("abcdef12-0000-0000-0000-000000000000", reminder_set=True)
        labels = [btn.text for btn in kb.inline_keyboard[0]]
        assert "✅ 🔔" in labels

    def test_callback_data_max_64_chars(self):
        # Telegram hard limit on callback_data is 64 bytes
        kb = feedback_keyboard("abcdef12-0000-0000-0000-000000000000")
        for btn in kb.inline_keyboard[0]:
            assert len(btn.callback_data.encode()) <= 64
