from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

import instaloader

from scraper.base import BaseScraper
from scraper.models import RawSignal, SourcePlatform

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


class InstagramScraper(BaseScraper):
    """
    Scrapes public Instagram posts by hashtag using instaloader.
    No credentials needed for public content — works out of the box.

    Note: Instagram rate-limits anonymous requests fairly aggressively.
    If you hit limits, log in with your Instagram account:
        loader.login("your_username", "your_password")
    """

    name = "instagram"

    def __init__(
        self,
        hashtags: list[str] = DEFAULT_HASHTAGS,
        max_posts_per_tag: int = 15,
    ) -> None:
        super().__init__()
        self.hashtags = hashtags
        self.max_posts_per_tag = max_posts_per_tag

    async def scrape(self) -> AsyncIterator[RawSignal]:
        # instaloader is synchronous — we run it inline
        # For a future async refactor, wrap with asyncio.to_thread()
        loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            quiet=True,
        )

        for tag in self.hashtags:
            self.logger.info(f"Scraping #{tag} on Instagram")
            count = 0
            try:
                hashtag = instaloader.Hashtag.from_name(loader.context, tag)
                for post in hashtag.get_posts():
                    if count >= self.max_posts_per_tag:
                        break
                    yield self._to_signal(post, tag)
                    count += 1
            except instaloader.exceptions.ConnectionException as e:
                self.logger.warning(f"Rate limited or connection error on #{tag}: {e}")
            except Exception as e:
                self.logger.error(f"Failed scraping #{tag} on Instagram: {e}")
            self.logger.debug(f"#{tag}: {count} posts")

    def _to_signal(self, post: instaloader.Post, hashtag: str) -> RawSignal:
        images: list[str] = []
        # display_url is the main image; sidecar_nodes covers carousel posts
        if post.url:
            images.append(post.url)
        if post.typename == "GraphSidecar":
            try:
                for node in post.get_sidecar_nodes():
                    if node.display_url and node.display_url not in images:
                        images.append(node.display_url)
            except Exception:
                pass  # Sidecar fetch is best-effort

        return RawSignal(
            id=post.shortcode,
            source_platform=SourcePlatform.INSTAGRAM,
            source_url=f"https://www.instagram.com/p/{post.shortcode}/",
            raw_text=post.caption,
            image_urls=images,
            fetched_at=datetime.utcnow(),
            metadata={
                "hashtag": hashtag,
                "author": post.owner_username,
                "likes": post.likes,
                "timestamp": post.date_utc.isoformat(),
                "location": post.location.name if post.location else None,
            },
        )
