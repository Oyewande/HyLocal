from __future__ import annotations

import os
from datetime import datetime
from typing import AsyncIterator

from apify_client import ApifyClient

from scraper.base import BaseScraper
from scraper.models import RawSignal, SourcePlatform

# Apify actor for Twitter/X scraping — handles auth and rate limits for us
TWITTER_ACTOR_ID = "apidojo/tweet-scraper"

DEFAULT_HASHTAGS = [
    "#LagosEvents",
    "#LagosWeekend",
    "#WhatsOnLagos",
    "#IkejaEvents",
    "#LagosFoodie",
    "#LagosNightlife",
    "#LagosMusic",
    "#LagosTech",
    "#LagosComedy",
]


class ApifyTwitterScraper(BaseScraper):
    """
    Scrapes Twitter/X by hashtag via Apify.
    Apify handles authentication, proxies, and rate limits.
    """

    name = "apify_twitter"

    def __init__(
        self,
        hashtags: list[str] = DEFAULT_HASHTAGS,
        max_tweets_per_tag: int = 20,
    ) -> None:
        super().__init__()
        self.hashtags = hashtags
        self.max_tweets_per_tag = max_tweets_per_tag
        self.api_token = os.getenv("APIFY_API_TOKEN", "")

    async def scrape(self) -> AsyncIterator[RawSignal]:
        if not self.api_token:
            self.logger.warning("APIFY_API_TOKEN not set — skipping Apify Twitter scraper")
            return

        client = ApifyClient(self.api_token)

        for tag in self.hashtags:
            self.logger.info(f"Scraping {tag} via Apify")
            try:
                run = client.actor(TWITTER_ACTOR_ID).call(
                    run_input={
                        "searchTerms": [tag],
                        "maxTweets": self.max_tweets_per_tag,
                        "addUserInfo": False,
                        "scrapeTweetReplies": False,
                    }
                )

                dataset_id: str = run["defaultDatasetId"]
                items = client.dataset(dataset_id).iterate_items()

                for tweet in items:
                    yield self._to_signal(tweet, tag)

            except Exception as e:
                self.logger.error(f"Apify Twitter scrape failed for {tag}: {e}")

    def _to_signal(self, tweet: dict, hashtag: str) -> RawSignal:
        tweet_id = tweet.get("id") or tweet.get("tweet_id", "")
        url = tweet.get("url") or f"https://twitter.com/i/web/status/{tweet_id}"

        # Apify may return images nested under 'media'
        images: list[str] = []
        for media in tweet.get("media", []):
            if isinstance(media, dict):
                img = media.get("media_url_https") or media.get("url", "")
                if img:
                    images.append(img)

        return RawSignal(
            id=str(tweet_id),
            source_platform=SourcePlatform.TWITTER,
            source_url=url,
            raw_text=tweet.get("full_text") or tweet.get("text"),
            image_urls=images,
            fetched_at=datetime.utcnow(),
            metadata={
                "hashtag": hashtag,
                "author": tweet.get("author", {}).get("userName", ""),
                "created_at": tweet.get("createdAt", ""),
                "likes": tweet.get("likeCount", 0),
                "retweets": tweet.get("retweetCount", 0),
            },
        )
