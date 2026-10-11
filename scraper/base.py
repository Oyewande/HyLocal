from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from scraper.models import RawSignal


class BaseScraper(ABC):
    """
    Abstract base for all scrapers. Each source (Instagram, X, a website)
    implements its own subclass. All scrapers yield RawSignal objects —
    the AI extraction layer handles the rest.
    """

    name: str = "base"

    def __init__(self) -> None:
        self.logger = logger.bind(scraper=self.name)

    @abstractmethod
    async def scrape(self) -> AsyncIterator[RawSignal]:
        """
        Yield raw signals from the source. Each signal is an unprocessed
        piece of content — a post, a page, a curator message.
        """
        ...

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def fetch_with_retry(self, url: str) -> str:
        """
        Shared HTTP fetch with automatic retry + exponential backoff.
        Subclasses that need custom fetch logic can override this.
        """
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.text

    async def run(self) -> list[RawSignal]:
        """
        Convenience wrapper: collects all signals from scrape() into a list.
        Use scrape() directly if you want streaming/async behaviour.
        """
        signals: list[RawSignal] = []
        self.logger.info(f"Starting scrape")

        try:
            async for signal in self.scrape():
                signals.append(signal)
                self.logger.debug(f"Got signal from {signal.source_platform}: {signal.source_url}")
        except Exception as e:
            self.logger.error(f"Scrape failed: {e}")
            raise

        self.logger.info(f"Finished — {len(signals)} signals collected")
        return signals
