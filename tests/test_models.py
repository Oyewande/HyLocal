"""
Tests for scraper/models.py — Pydantic model validation.
"""
from __future__ import annotations

import pytest
from scraper.models import (
    EventCategory,
    ExtractedEvent,
    RawSignal,
    SourcePlatform,
    SocialVibe,
)


class TestRawSignal:
    def test_defaults(self):
        sig = RawSignal(source_platform=SourcePlatform.TWITTER)
        assert sig.image_urls == []
        assert sig.metadata == {}
        assert sig.id is None

    def test_with_text(self):
        sig = RawSignal(
            source_platform=SourcePlatform.INSTAGRAM,
            raw_text="Come join us this Friday!",
            source_url="https://instagram.com/p/abc123",
        )
        assert sig.raw_text == "Come join us this Friday!"
        assert sig.source_platform == SourcePlatform.INSTAGRAM

    def test_website_platform(self):
        sig = RawSignal(
            source_platform=SourcePlatform.WEBSITE,
            source_url="https://eventbrite.com/e/test",
        )
        assert sig.source_platform == SourcePlatform.WEBSITE


class TestExtractedEvent:
    def test_minimal(self):
        event = ExtractedEvent(name="Test Event")
        assert event.name == "Test Event"
        assert event.is_free is False
        assert event.is_scarce is False
        assert event.categories == []
        assert event.confidence == 0.0
        assert event.needs_review is True

    def test_confidence_clamped_high(self):
        event = ExtractedEvent(name="Event", confidence=2.0)
        assert event.confidence == pytest.approx(1.0)

    def test_confidence_clamped_low(self):
        event = ExtractedEvent(name="Event", confidence=-1.0)
        assert event.confidence == pytest.approx(0.0)

    def test_valid_categories(self):
        event = ExtractedEvent(
            name="Event",
            categories=[EventCategory.MUSIC, EventCategory.FOOD],
        )
        assert len(event.categories) == 2

    def test_ticket_url(self):
        event = ExtractedEvent(
            name="Event",
            ticket_url="https://paystack.com/buy/tickets",
        )
        assert event.ticket_url == "https://paystack.com/buy/tickets"

    def test_vibe(self):
        event = ExtractedEvent(name="Event", vibe=SocialVibe.PARTY)
        assert event.vibe == SocialVibe.PARTY


class TestEnums:
    def test_all_categories_exist(self):
        expected = {"music","comedy","tech","fitness","food","art","networking","gaming","popup","other"}
        actual = {c.value for c in EventCategory}
        assert expected == actual

    def test_all_vibes_exist(self):
        expected = {"chill","social","professional","party","active","intimate"}
        actual = {v.value for v in SocialVibe}
        assert expected == actual

    def test_all_platforms_exist(self):
        expected = {"instagram","twitter","website","google_places","curator","manual"}
        actual = {p.value for p in SourcePlatform}
        assert expected == actual
