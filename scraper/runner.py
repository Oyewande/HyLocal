from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Type

from dotenv import load_dotenv

# Load .env BEFORE importing scrapers — os.getenv() in scraper modules
# is evaluated at import time, so dotenv must run first.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from loguru import logger  # noqa: E402

from scraper.base import BaseScraper  # noqa: E402
from scraper.eventbrite import EventbriteScraper  # noqa: E402
from scraper.instagram import InstagramScraper  # noqa: E402
from scraper.models import RawSignal  # noqa: E402
from scraper.twitter import TwitterScraper  # noqa: E402

# Register all active scrapers here.
# Comment out any you want to temporarily disable.
ACTIVE_SCRAPERS: list[Type[BaseScraper]] = [
    TwitterScraper,
    InstagramScraper,
    EventbriteScraper,
]


async def run_all_scrapers() -> list[RawSignal]:
    """
    Runs all active scrapers concurrently and returns the combined
    list of raw signals for the extraction layer to process.
    """
    tasks = [scraper().run() for scraper in ACTIVE_SCRAPERS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_signals: list[RawSignal] = []
    for scraper_cls, result in zip(ACTIVE_SCRAPERS, results):
        if isinstance(result, Exception):
            logger.error(f"{scraper_cls.name} scraper failed: {result}")
        else:
            all_signals.extend(result)
            logger.info(f"{scraper_cls.name}: {len(result)} signals")

    logger.info(f"Total signals collected: {len(all_signals)}")
    return all_signals


if __name__ == "__main__":
    signals = asyncio.run(run_all_scrapers())
    for s in signals:
        print(f"\n[{s.source_platform.value.upper()}] {s.source_url}")
        if s.raw_text:
            print(f"  {s.raw_text[:120]}...")
        if s.image_urls:
            print(f"  Images: {len(s.image_urls)}")
