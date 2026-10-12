"""
Tests for processor/extractor.py

We test the JSON parsing and _build_event logic in isolation —
no live AI calls needed.
"""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from scraper.models import RawSignal, SourcePlatform, EventCategory, SocialVibe


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_signal(text: str = "test") -> RawSignal:
    return RawSignal(
        source_platform=SourcePlatform.TWITTER,
        source_url="https://twitter.com/test/status/1",
        raw_text=text,
    )


def make_extractor():
    """
    Import Extractor without triggering __init__ or needing live API packages.
    We stub out 'anthropic' and 'openai' in sys.modules so the import succeeds,
    then instantiate via __new__ to bypass the key-check __init__.
    """
    # Stub missing packages so the import doesn't fail in the test environment
    for mod_name in ("anthropic", "openai"):
        if mod_name not in sys.modules:
            stub = types.ModuleType(mod_name)
            stub.Anthropic = MagicMock()
            stub.APIStatusError = Exception
            stub.APIError = Exception
            stub.OpenAI = MagicMock()
            sys.modules[mod_name] = stub

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        # Remove cached import if present so env patch takes effect
        sys.modules.pop("processor.extractor", None)
        from processor.extractor import Extractor
        ext = Extractor.__new__(Extractor)
        ext._anthropic = None
        ext._openrouter = None
        return ext


# ── _parse_json tests ─────────────────────────────────────────────────────────

class TestParseJson:
    def setup_method(self):
        self.ext = make_extractor()
        self.signal = make_signal()

    def test_direct_json(self):
        raw = json.dumps({"name": "Test Event", "confidence": 0.9})
        result = self.ext._parse_json(raw, self.signal)
        assert result["name"] == "Test Event"

    def test_strips_markdown_fence(self):
        raw = "```json\n{\"name\": \"Fenced Event\", \"confidence\": 0.8}\n```"
        result = self.ext._parse_json(raw, self.signal)
        assert result["name"] == "Fenced Event"

    def test_strips_plain_fence(self):
        raw = "```\n{\"name\": \"Plain Fence\", \"confidence\": 0.7}\n```"
        result = self.ext._parse_json(raw, self.signal)
        assert result["name"] == "Plain Fence"

    def test_extracts_first_brace_block(self):
        raw = 'Here is the result: {"name": "Brace Event", "confidence": 0.6} Hope that helps!'
        result = self.ext._parse_json(raw, self.signal)
        assert result["name"] == "Brace Event"

    def test_raises_on_no_json(self):
        with pytest.raises(ValueError, match="No valid JSON"):
            self.ext._parse_json("This is not JSON at all", self.signal)


# ── _build_event tests ────────────────────────────────────────────────────────

class TestBuildEvent:
    def setup_method(self):
        self.ext = make_extractor()
        self.signal = make_signal()

    def _build(self, data: dict):
        return self.ext._build_event(data, self.signal)

    def test_basic_fields(self):
        event = self._build({
            "name": "Lagos Tech Meetup",
            "description": "Annual tech gathering",
            "date_raw": "this Friday",
            "location_raw": "Co-Creation Hub, Yaba",
            "is_free": True,
            "confidence": 0.9,
        })
        assert event.name == "Lagos Tech Meetup"
        assert event.description == "Annual tech gathering"
        assert event.date_raw == "this Friday"
        assert event.location_raw == "Co-Creation Hub, Yaba"
        assert event.is_free is True
        assert event.confidence == pytest.approx(0.9)
        assert event.needs_review is False  # 0.9 >= 0.8

    def test_needs_review_below_threshold(self):
        event = self._build({"name": "Sketchy Event", "confidence": 0.6})
        assert event.needs_review is True

    def test_invalid_category_ignored(self):
        event = self._build({
            "name": "Event",
            "categories": ["music", "notavalidcat", "tech"],
            "confidence": 0.5,
        })
        assert EventCategory.MUSIC in event.categories
        assert EventCategory.TECH in event.categories
        assert len(event.categories) == 2  # invalid one dropped

    def test_invalid_vibe_becomes_none(self):
        event = self._build({"name": "Event", "vibe": "raving", "confidence": 0.5})
        assert event.vibe is None

    def test_valid_vibe(self):
        event = self._build({"name": "Event", "vibe": "chill", "confidence": 0.5})
        assert event.vibe == SocialVibe.CHILL

    def test_confidence_clamped(self):
        event = self._build({"name": "Event", "confidence": 1.5})
        assert event.confidence == pytest.approx(1.0)
        event2 = self._build({"name": "Event", "confidence": -0.5})
        assert event2.confidence == pytest.approx(0.0)

    def test_ticket_url_and_scarce(self):
        event = self._build({
            "name": "Hot Event",
            "ticket_url": "https://paystack.com/buy/hot-event",
            "is_scarce": True,
            "confidence": 0.9,
        })
        assert event.ticket_url == "https://paystack.com/buy/hot-event"
        assert event.is_scarce is True

    def test_missing_name_defaults(self):
        event = self._build({"confidence": 0.5})
        assert event.name == "Unnamed Event"

    def test_source_url_passed_through(self):
        event = self._build({"name": "Event", "confidence": 0.5})
        assert event.source_url == "https://twitter.com/test/status/1"
        assert event.source_platform == SourcePlatform.TWITTER
