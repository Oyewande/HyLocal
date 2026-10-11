from __future__ import annotations

import os
from datetime import datetime
from typing import AsyncIterator

from apify_client import ApifyClient

from scraper.base import BaseScraper
from scraper.models import RawSignal, SourcePlatform

# Official Apify Instagram scraper
INSTAGRAM_ACTOR_ID = "apify/instagram-scraper"

DEFAULT_HASHTAGS = [
    "lagosevents",
    "lagosweekend",
    "whatsonlagos",
    "ikejalagos",
    "lagoslife",
    "lagosfoodie",
    "lagosnightlife",
    "lagosmusic",
    "lagostech",
    "lagoscomedy",
]


class ApifyInstagramScraper(BaseScraper):
    """
    Scrapes Instagram posts by hashtag via Apify.
    Returns posts including caption text and image URLs —
    captions often contain full event details that the AI extractor parses.
    """

    name = "apify_instagram"

    def __init__(
        self,
        hashtags: list[str] = DEFAULT_HASHTAGS,
        max_posts_per_tag: int = 15,
    ) -> None:
        super().__init__()
        self.hashtags = hashtags
        self.max_posts_per_tag = max_posts_per_tag
        self.api_token = os.getenv("APIFY_API_TOKEN", "")

    async def scrape(self) -> AsyncIterator[RawSignal]:
        if not self.api_token:
            self.logger.warning("APIFY_API_TOKEN not set — skipping Apify Instagram scraper")
            return

        client = ApifyClient(self.api_token)

        # Instagram scraper takes hashtag URLs as input
        hashtag_urls = [
            f"https://www.instagram.com/explore/tags/{tag}/"
            for tag in self.hashtags
        ]

        self.logger.info(f"Scraping {len(self.hashtags)} Instagram hashtags via Apify")
        try:
            run = client.actor(INSTAGRAM_ACTOR_ID).call(
                run_input={
                    "directUrls": hashtag_urls,
                    "resultsType": "posts",
                    "resultsLimit": self.max_posts_per_tag,
                    "addParentData": False,
                }
            )

            dataset_id: str = run["defaultDatasetId"]
            items = client.dataset(dataset_id).iterate_items()

            for post in items:
                yield self._to_signal(post)

        except Exception as e:
            self.logger.error(f"Apify Instagram scrape failed: {e}")

    def _to_signal(self, post: dict) -> RawSignal:
        # Collect all image URLs from the post (carousels have multiple)
        images: list[str] = []
        display_url = post.get("displayUrl")
        if display_url:
            images.append(display_url)
        for sidecar in post.get("sidecars", []):
            if isinstance(sidecar, dict):
                url = sidecar.get("displayUrl", "")
                if url:
                    images.append(url)

        return RawSignal(
            id=post.get("id", ""),
            source_platform=SourcePlatform.INSTAGRAM,
            source_url=post.get("url") or f"https://www.instagram.com/p/{post.get('shortCode', '')}",
            raw_text=post.get("caption"),
            image_urls=images,
            fetched_at=datetime.utcnow(),
            metadata={
                "hashtags": post.get("hashtags", []),
                "author": post.get("ownerUsername", ""),
                "likes": post.get("likesCount", 0),
                "timestamp": post.get("timestamp", ""),
                "location": post.get("locationName"),
            },
        )
