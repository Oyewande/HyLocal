from __future__ import annotations

import os
from datetime import datetime
from typing import AsyncIterator

import twscrape

from scraper.base import BaseScraper
from scraper.models import RawSignal, SourcePlatform

DEFAULT_HASHTAGS = [
    "LagosEvents",
    "LagosWeekend",
    "WhatsOnLagos",
    "IkejaEvents",
    "LagosFoodie",
    "LagosNightlife",
    "LagosMusic",
    "LagosTech",
    "LagosComedy",
]


class TwitterScraper(BaseScraper):
    """
    Scrapes Twitter/X by hashtag using twscrape.
    Uses a regular Twitter account — no developer account or payment needed.

    First-time setup (run once in your terminal):
        twscrape add_accounts twitter_accounts.txt
        twscrape login_accounts

    twitter_accounts.txt format (one per line):
        username:password:email:email_password
    """

    name = "twitter"

    def __init__(
        self,
        hashtags: list[str] = DEFAULT_HASHTAGS,
        max_tweets_per_tag: int = 20,
    ) -> None:
        super().__init__()
        self.hashtags = hashtags
        self.max_tweets_per_tag = max_tweets_per_tag

    async def scrape(self) -> AsyncIterator[RawSignal]:
        api = twscrape.API()

        # Check we have at least one logged-in account before scraping
        accounts = await api.pool.get_all()
        active = [a for a in accounts if a.active]
        if not active:
            self.logger.warning(
                "No active Twitter accounts in twscrape pool. "
                "Run: twscrape add_accounts twitter_accounts.txt && twscrape login_accounts"
            )
            return

        self.logger.info(f"Scraping {len(self.hashtags)} hashtags ({len(active)} active accounts)")

        for tag in self.hashtags:
            self.logger.info(f"Searching #{tag}")
            count = 0
            try:
                async for tweet in api.search(f"#{tag} -filter:retweets", limit=self.max_tweets_per_tag):
                    yield self._to_signal(tweet, tag)
                    count += 1
            except Exception as e:
                self.logger.error(f"Failed scraping #{tag}: {e}")
            self.logger.debug(f"#{tag}: {count} tweets")

    def _to_signal(self, tweet: twscrape.Tweet, hashtag: str) -> RawSignal:
        images: list[str] = []
        if tweet.media:
            for m in tweet.media.photos:
                images.append(m.url)

        return RawSignal(
            id=str(tweet.id),
            source_platform=SourcePlatform.TWITTER,
            source_url=tweet.url,
            raw_text=tweet.rawContent,
            image_urls=images,
            fetched_at=datetime.utcnow(),
            metadata={
                "hashtag": hashtag,
                "author": tweet.user.username if tweet.user else "",
                "likes": tweet.likeCount,
                "retweets": tweet.retweetCount,
                "created_at": tweet.date.isoformat() if tweet.date else "",
            },
        )
