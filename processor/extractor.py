from __future__ import annotations

import json
import os
from datetime import datetime

import anthropic
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from scraper.models import (
    EventCategory,
    ExtractedEvent,
    RawSignal,
    SocialVibe,
    SourcePlatform,
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

EXTRACTION_PROMPT = """You are an event extraction assistant for a Lagos, Nigeria hyperlocal discovery app.

Extract structured event information from the content below. The content may be a social media post, image caption, or informal announcement.

Return ONLY valid JSON matching this schema:
{
  "name": "Event name (string, required)",
  "description": "Brief description (string or null)",
  "date_raw": "Date as written in the post e.g. 'this Saturday', 'May 17' (string or null)",
  "location_raw": "Location as written e.g. 'Terra Kulture, VI' (string or null)",
  "cost_raw": "Cost as written e.g. '₦5,000 at door', 'Free entry' (string or null)",
  "cost_min": "Minimum cost in NGN as integer, 0 if free (integer or null)",
  "cost_max": "Maximum cost in NGN as integer if range given (integer or null)",
  "is_free": "true if free entry (boolean)",
  "categories": ["array of categories from: music, comedy, tech, fitness, food, art, networking, gaming, popup, other"],
  "vibe": "one of: chill, social, professional, party, active, intimate (string or null)",
  "confidence": "your confidence 0.0-1.0 that this is actually an event (float)"
}

Rules:
- If the content is NOT an event (e.g. a generic post, ad, news), return confidence below 0.3
- Lagos neighbourhoods to recognise: Ikeja, Ketu, Magodo, Berger, Yaba, Lekki, VI, Ikoyi, Surulere, Maryland, Gbagada
- Cost: ₦ and NGN are naira. K means thousands (₦5k = 5000)
- If no date is mentioned, set date_raw to null
- name is required — use the event type if no name given (e.g. "Comedy Night")

Content to extract from:
{content}"""


class Extractor:
    """
    Takes a RawSignal and returns a structured ExtractedEvent using Claude.
    Handles text posts, captions, and image-based content (flyers).
    """

    def __init__(self) -> None:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def extract(self, signal: RawSignal) -> ExtractedEvent | None:
        """
        Extract a structured event from a raw signal.
        Returns None if the signal clearly isn't an event (low confidence).
        """
        content_parts: list[str] = []

        if signal.raw_text:
            content_parts.append(f"Text: {signal.raw_text}")

        if signal.metadata.get("location"):
            content_parts.append(f"Tagged location: {signal.metadata['location']}")

        if signal.metadata.get("hashtags"):
            content_parts.append(f"Hashtags: {', '.join(signal.metadata['hashtags'])}")

        if not content_parts and not signal.image_urls:
            logger.debug(f"Signal {signal.id} has no extractable content — skipping")
            return None

        content = "\n".join(content_parts) if content_parts else "(image only — no caption)"

        try:
            extracted = self._call_claude(content, signal)
            if extracted.confidence < 0.3:
                logger.debug(
                    f"Signal {signal.id} confidence {extracted.confidence:.2f} too low — not an event"
                )
                return None
            return extracted
        except Exception as e:
            logger.error(f"Extraction failed for signal {signal.id}: {e}")
            return None

    @retry(
        retry=retry_if_exception_type(anthropic.APIStatusError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _call_claude(self, content: str, signal: RawSignal) -> ExtractedEvent:
        prompt = EXTRACTION_PROMPT.format(content=content)

        message = self.client.messages.create(
            model="claude-haiku-5-5",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_json = message.content[0].text.strip()

        # Strip markdown code fences if Claude wrapped the JSON
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]

        try:
            data: dict = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.error(
                f"JSON parse failed for signal {signal.id}.\n"
                f"Error: {e}\n"
                f"Raw response: {raw_json[:500]}"
            )
            raise

        return ExtractedEvent(
            name=data.get("name", "Unnamed Event"),
            description=data.get("description"),
            date_raw=data.get("date_raw"),
            location_raw=data.get("location_raw"),
            cost_raw=data.get("cost_raw"),
            cost_min=data.get("cost_min"),
            cost_max=data.get("cost_max"),
            is_free=data.get("is_free", False),
            categories=[
                EventCategory(c)
                for c in data.get("categories", [])
                if c in EventCategory._value2member_map_
            ],
            vibe=SocialVibe(data["vibe"]) if data.get("vibe") in SocialVibe._value2member_map_ else None,
            source_platform=signal.source_platform,
            source_url=signal.source_url,
            confidence=float(data.get("confidence", 0.5)),
            needs_review=float(data.get("confidence", 0.5)) < 0.8,
        )
