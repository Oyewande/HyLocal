from __future__ import annotations

from datetime import datetime, timezone

import dateparser
from loguru import logger

from processor.extractor import Extractor
from processor.geocoder import Geocoder
from scraper.models import ExtractedEvent, RawSignal

# Lagos is UTC+1 year-round (no DST)
_LAGOS_SETTINGS = {
    "TIMEZONE": "Africa/Lagos",
    "RETURN_AS_TIMEZONE_AWARE": True,
    "PREFER_DATES_FROM": "future",
}


class Pipeline:
    """
    Orchestrates the full processing flow for a batch of raw signals:
      1. Extract structured event data via AI (Extractor)
      2. Geocode the location (Geocoder)
      3. Filter low-confidence and duplicate results

    Returns a list of ExtractedEvents ready to be stored.
    """

    def __init__(self) -> None:
        self.extractor = Extractor()
        self.geocoder = Geocoder()

    def process(self, signals: list[RawSignal]) -> list[ExtractedEvent]:
        logger.info(f"Processing {len(signals)} raw signals")
        events: list[ExtractedEvent] = []
        seen_urls: set[str] = set()

        for signal in signals:
            # Deduplicate by source URL
            if signal.source_url and signal.source_url in seen_urls:
                logger.debug(f"Skipping duplicate signal: {signal.source_url}")
                continue
            if signal.source_url:
                seen_urls.add(signal.source_url)

            event = self.extractor.extract(signal)
            if event is None:
                continue

            # Resolve date_raw → datetime
            if event.date_raw and event.date is None:
                parsed = dateparser.parse(event.date_raw, settings=_LAGOS_SETTINGS)
                if parsed:
                    event.date = parsed
                    logger.debug(f"Resolved date '{event.date_raw}' → {parsed.isoformat()}")

            # Geocode if we have a raw location
            if event.location_raw:
                coords = self.geocoder.geocode(event.location_raw)
                if coords:
                    event.location_lat, event.location_lng = coords

            events.append(event)
            logger.info(
                f"Extracted: '{event.name}' | "
                f"confidence={event.confidence:.2f} | "
                f"location={event.location_raw} | "
                f"needs_review={event.needs_review}"
            )

        logger.info(f"Pipeline complete: {len(events)} events from {len(signals)} signals")
        return events
