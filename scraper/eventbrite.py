"""
Eventbrite Lagos scraper
========================
Scrapes Eventbrite's public Lagos search pages for upcoming events.
No API key required — reads the same HTML/JSON the browser sees.

Strategy (in order):
  1. Parse window.__SERVER_DATA__ JSON blob embedded by Eventbrite's SSR.
  2. Fall back to JSON-LD <script type="application/ld+json"> blocks.
  3. Fall back to BeautifulSoup HTML card parsing.

Rate limiting: one request per CRAWL_DELAY seconds (default 5).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from scraper.base import BaseScraper
from scraper.models import RawSignal, SourcePlatform

# Lagos search URLs — covers city-wide + key local-government areas
_SEARCH_URLS = [
    "https://www.eventbrite.com/d/nigeria--lagos/events/",
    "https://www.eventbrite.com/d/nigeria--lagos--ikeja/events/",
    "https://www.eventbrite.com/d/nigeria--lagos--lekki/events/",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

CRAWL_DELAY = 5  # seconds between page requests


class EventbriteScraper(BaseScraper):
    name = "eventbrite"

    async def scrape(self) -> AsyncIterator[RawSignal]:
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=20,
            follow_redirects=True,
        ) as client:
            for url in _SEARCH_URLS:
                self.logger.info(f"Fetching {url}")
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    html = resp.text
                except httpx.HTTPError as e:
                    self.logger.warning(f"HTTP error for {url}: {e}")
                    await asyncio.sleep(CRAWL_DELAY)
                    continue

                events = (
                    _parse_server_data(html)
                    or _parse_jsonld(html)
                    or _parse_html_cards(html)
                )

                for event in events:
                    event_url = event.get("url") or event.get("source_url") or ""
                    if event_url in seen_urls:
                        continue
                    seen_urls.add(event_url)

                    signal = _to_signal(event)
                    if signal:
                        yield signal

                await asyncio.sleep(CRAWL_DELAY)


# ── Parsing strategies ────────────────────────────────────────────────────────

def _parse_server_data(html: str) -> list[dict]:
    """
    Eventbrite embeds the full search result as:
      window.__SERVER_DATA__ = {...};
    The events live under search_data.events.results[].
    """
    match = re.search(
        r"window\.__SERVER_DATA__\s*=\s*(\{.*?\});\s*</script>",
        html,
        re.DOTALL,
    )
    if not match:
        return []

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    # Navigate to the events list — Eventbrite nests it differently by page type
    results = (
        data.get("search_data", {})
            .get("events", {})
            .get("results", [])
    )
    if not results:
        # Alternative path used on some pages
        results = (
            data.get("events", {})
                .get("results", [])
        )

    events = []
    for r in results:
        event = {
            "name": r.get("name") or r.get("title"),
            "description": (r.get("summary") or r.get("description") or "")[:500],
            "date_raw": _parse_eb_date(r),
            "location_raw": _parse_eb_location(r),
            "is_free": r.get("is_free", False),
            "cost_raw": _parse_eb_cost(r),
            "ticket_url": r.get("url"),
            "source_url": r.get("url"),
            "image_url": r.get("image", {}).get("url") if r.get("image") else None,
        }
        if event["name"]:
            events.append(event)

    return events


def _parse_jsonld(html: str) -> list[dict]:
    """
    Eventbrite outputs JSON-LD Event schema blocks that look like:
      {"@type": "Event", "name": "...", "startDate": "...", ...}
    """
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    events = []
    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get("@type") != "Event":
                continue

            location = item.get("location", {})
            loc_name = location.get("name", "")
            loc_addr = location.get("address", {})
            if isinstance(loc_addr, dict):
                loc_str = ", ".join(filter(None, [
                    loc_addr.get("streetAddress"),
                    loc_addr.get("addressLocality"),
                    loc_addr.get("addressRegion"),
                ]))
            else:
                loc_str = str(loc_addr)

            offers = item.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            is_free = str(offers.get("price", "1")) == "0"
            price = offers.get("price")
            cost_raw = None if is_free else (f"₦{price}" if price else None)

            events.append({
                "name": item.get("name"),
                "description": (item.get("description") or "")[:500],
                "date_raw": item.get("startDate"),
                "location_raw": loc_name or loc_str or None,
                "is_free": is_free,
                "cost_raw": cost_raw,
                "ticket_url": item.get("url"),
                "source_url": item.get("url"),
            })

    return events


def _parse_html_cards(html: str) -> list[dict]:
    """
    Last-resort: parse event cards from rendered HTML.
    Eventbrite card selectors as of 2024/25:
      <section data-testid="event-card"> or <article class="eds-event-card">
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = (
        soup.find_all("section", attrs={"data-testid": "event-card"})
        or soup.find_all("article", class_=re.compile(r"eds-event-card"))
        or soup.find_all("div", class_=re.compile(r"search-event-card"))
    )

    events = []
    for card in cards:
        # Title
        title_el = (
            card.find("h2")
            or card.find("h3")
            or card.find(attrs={"data-testid": "event-card-title"})
        )
        name = title_el.get_text(strip=True) if title_el else None
        if not name:
            continue

        # Date
        date_el = card.find(attrs={"data-testid": "event-card-date"}) or card.find("time")
        date_raw = date_el.get_text(strip=True) if date_el else None

        # Location
        loc_el = card.find(attrs={"data-testid": "event-card-location"})
        location_raw = loc_el.get_text(strip=True) if loc_el else None

        # Price
        price_el = card.find(attrs={"data-testid": "event-card-price"})
        price_text = price_el.get_text(strip=True) if price_el else ""
        is_free = "free" in price_text.lower()
        cost_raw = None if is_free else (price_text or None)

        # URL — links on the card
        link = card.find("a", href=re.compile(r"eventbrite\.com/e/"))
        source_url = link["href"] if link else None

        events.append({
            "name": name,
            "description": None,
            "date_raw": date_raw,
            "location_raw": location_raw,
            "is_free": is_free,
            "cost_raw": cost_raw,
            "ticket_url": source_url,
            "source_url": source_url,
        })

    return events


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_eb_date(r: dict) -> str | None:
    """Extract a readable date string from an Eventbrite result dict."""
    start = r.get("start_date") or r.get("start", {})
    if isinstance(start, dict):
        local = start.get("local") or start.get("utc") or ""
        return local[:16].replace("T", " ") if local else None
    return str(start) if start else None


def _parse_eb_location(r: dict) -> str | None:
    venue = r.get("primary_venue") or r.get("venue") or {}
    if isinstance(venue, dict):
        parts = [
            venue.get("name"),
            venue.get("address", {}).get("localized_area_display"),
        ]
        return ", ".join(p for p in parts if p) or None
    return None


def _parse_eb_cost(r: dict) -> str | None:
    cost = r.get("cost") or r.get("ticket_availability", {})
    if isinstance(cost, dict):
        display = cost.get("minimum_ticket_price", {}).get("display")
        return display or None
    return None


def _to_signal(event: dict) -> RawSignal | None:
    """Convert a parsed event dict to a RawSignal."""
    parts = []
    if event.get("name"):
        parts.append(event["name"])
    if event.get("date_raw"):
        parts.append(f"Date: {event['date_raw']}")
    if event.get("location_raw"):
        parts.append(f"Location: {event['location_raw']}")
    if event.get("description"):
        parts.append(event["description"])
    if event.get("is_free"):
        parts.append("Free event")
    elif event.get("cost_raw"):
        parts.append(f"Cost: {event['cost_raw']}")
    if event.get("ticket_url"):
        parts.append(f"Tickets: {event['ticket_url']}")

    raw_text = "\n".join(parts)
    if not raw_text.strip():
        return None

    images = []
    if event.get("image_url"):
        images.append(event["image_url"])

    return RawSignal(
        source_platform=SourcePlatform.WEBSITE,
        source_url=event.get("source_url"),
        raw_text=raw_text,
        image_urls=images,
        metadata={"source": "eventbrite"},
    )
