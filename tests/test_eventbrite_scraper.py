"""
Tests for scraper/eventbrite.py

All parsing logic is pure (no network calls) so these run without internet.
"""
from __future__ import annotations

import json
import pytest

from scraper.eventbrite import (
    _parse_jsonld,
    _parse_html_cards,
    _parse_server_data,
    _to_signal,
)


# ── JSON-LD parsing ───────────────────────────────────────────────────────────

JSONLD_EVENT = {
    "@context": "http://schema.org",
    "@type": "Event",
    "name": "Lagos Jazz Night",
    "startDate": "2026-10-18T20:00:00",
    "location": {
        "@type": "Place",
        "name": "Terra Kulture",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Plot 1376, Tiamiyu Savage Street",
            "addressLocality": "Victoria Island",
        },
    },
    "offers": {"@type": "Offer", "price": "5000", "priceCurrency": "NGN"},
    "url": "https://www.eventbrite.com/e/lagos-jazz-night-123",
}


def html_with_jsonld(data: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


class TestParseJsonLd:
    def test_extracts_name(self):
        events = _parse_jsonld(html_with_jsonld(JSONLD_EVENT))
        assert len(events) == 1
        assert events[0]["name"] == "Lagos Jazz Night"

    def test_extracts_date(self):
        events = _parse_jsonld(html_with_jsonld(JSONLD_EVENT))
        assert events[0]["date_raw"] == "2026-10-18T20:00:00"

    def test_extracts_location(self):
        events = _parse_jsonld(html_with_jsonld(JSONLD_EVENT))
        assert "Terra Kulture" in events[0]["location_raw"]

    def test_extracts_cost(self):
        events = _parse_jsonld(html_with_jsonld(JSONLD_EVENT))
        assert "5000" in events[0]["cost_raw"]
        assert events[0]["is_free"] is False

    def test_free_event(self):
        data = {**JSONLD_EVENT, "offers": {"@type": "Offer", "price": "0"}}
        events = _parse_jsonld(html_with_jsonld(data))
        assert events[0]["is_free"] is True

    def test_ticket_url(self):
        events = _parse_jsonld(html_with_jsonld(JSONLD_EVENT))
        assert events[0]["ticket_url"] == "https://www.eventbrite.com/e/lagos-jazz-night-123"

    def test_ignores_non_event_schema(self):
        data = {"@type": "Organization", "name": "Acme Corp"}
        events = _parse_jsonld(html_with_jsonld(data))
        assert len(events) == 0

    def test_handles_multiple_blocks(self):
        block1 = html_with_jsonld({**JSONLD_EVENT, "name": "Event One"})
        block2 = html_with_jsonld({**JSONLD_EVENT, "name": "Event Two"})
        events = _parse_jsonld(block1 + block2)
        names = {e["name"] for e in events}
        assert names == {"Event One", "Event Two"}

    def test_empty_html_returns_empty(self):
        assert _parse_jsonld("<html><body>No events here</body></html>") == []

    def test_malformed_json_skipped(self):
        bad = '<script type="application/ld+json">NOT JSON</script>'
        events = _parse_jsonld(bad)
        assert len(events) == 0


# ── HTML card parsing ─────────────────────────────────────────────────────────

CARD_HTML = """
<section data-testid="event-card">
  <h2>Afrobeats Fest</h2>
  <div data-testid="event-card-date">Saturday, October 19 · 6:00 PM WAT</div>
  <div data-testid="event-card-location">Eko Hotels, Victoria Island</div>
  <div data-testid="event-card-price">₦10,000 – ₦25,000</div>
  <a href="https://www.eventbrite.com/e/afrobeats-fest-987">Get tickets</a>
</section>
"""

FREE_CARD_HTML = """
<section data-testid="event-card">
  <h2>Community Art Show</h2>
  <div data-testid="event-card-date">Sunday, October 20 · 2:00 PM WAT</div>
  <div data-testid="event-card-location">Nike Art Gallery, Lekki</div>
  <div data-testid="event-card-price">Free</div>
  <a href="https://www.eventbrite.com/e/art-show-456">RSVP</a>
</section>
"""


class TestParseHtmlCards:
    def test_extracts_name(self):
        events = _parse_html_cards(CARD_HTML)
        assert len(events) == 1
        assert events[0]["name"] == "Afrobeats Fest"

    def test_extracts_date(self):
        events = _parse_html_cards(CARD_HTML)
        assert "Saturday" in events[0]["date_raw"]

    def test_extracts_location(self):
        events = _parse_html_cards(CARD_HTML)
        assert "Eko Hotels" in events[0]["location_raw"]

    def test_paid_event(self):
        events = _parse_html_cards(CARD_HTML)
        assert events[0]["is_free"] is False
        assert events[0]["cost_raw"] is not None

    def test_free_event(self):
        events = _parse_html_cards(FREE_CARD_HTML)
        assert events[0]["is_free"] is True

    def test_source_url(self):
        events = _parse_html_cards(CARD_HTML)
        assert events[0]["source_url"] == "https://www.eventbrite.com/e/afrobeats-fest-987"

    def test_multiple_cards(self):
        events = _parse_html_cards(CARD_HTML + FREE_CARD_HTML)
        assert len(events) == 2

    def test_empty_html(self):
        assert _parse_html_cards("<html><body></body></html>") == []


# ── __SERVER_DATA__ parsing ───────────────────────────────────────────────────

SERVER_DATA = {
    "search_data": {
        "events": {
            "results": [
                {
                    "name": "Server Data Event",
                    "summary": "A great event",
                    "start_date": "2026-10-20",
                    "primary_venue": {
                        "name": "Landmark Centre",
                        "address": {"localized_area_display": "Oniru, Lagos"},
                    },
                    "is_free": False,
                    "cost": {"minimum_ticket_price": {"display": "₦8,000"}},
                    "url": "https://www.eventbrite.com/e/server-data-event-111",
                }
            ]
        }
    }
}


class TestParseServerData:
    def _html(self, data: dict) -> str:
        js = json.dumps(data)
        return f"<script>window.__SERVER_DATA__ = {js};</script>"

    def test_extracts_name(self):
        events = _parse_server_data(self._html(SERVER_DATA))
        assert len(events) == 1
        assert events[0]["name"] == "Server Data Event"

    def test_extracts_location(self):
        events = _parse_server_data(self._html(SERVER_DATA))
        assert "Landmark Centre" in events[0]["location_raw"]

    def test_extracts_ticket_url(self):
        events = _parse_server_data(self._html(SERVER_DATA))
        assert "eventbrite.com" in events[0]["ticket_url"]

    def test_missing_server_data_returns_empty(self):
        events = _parse_server_data("<html><body>no data</body></html>")
        assert events == []


# ── _to_signal ────────────────────────────────────────────────────────────────

class TestToSignal:
    def test_builds_signal_from_event(self):
        ev = {
            "name": "Test",
            "date_raw": "this Saturday",
            "location_raw": "Ikeja",
            "is_free": True,
            "ticket_url": "https://eventbrite.com/e/test-123",
            "source_url": "https://eventbrite.com/e/test-123",
            "description": None,
            "image_url": None,
        }
        sig = _to_signal(ev)
        assert sig is not None
        assert "Test" in sig.raw_text
        assert "this Saturday" in sig.raw_text
        assert "Ikeja" in sig.raw_text
        assert sig.source_url == "https://eventbrite.com/e/test-123"

    def test_empty_event_returns_none(self):
        sig = _to_signal({"name": None, "description": None, "source_url": None})
        assert sig is None

    def test_image_url_included(self):
        ev = {
            "name": "Art Show",
            "date_raw": None,
            "location_raw": None,
            "is_free": False,
            "cost_raw": None,
            "ticket_url": None,
            "source_url": "https://eventbrite.com/e/art-123",
            "description": None,
            "image_url": "https://img.evbuc.com/art.jpg",
        }
        sig = _to_signal(ev)
        assert "https://img.evbuc.com/art.jpg" in sig.image_urls
