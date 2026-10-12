from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, HttpUrl, field_validator


class EventCategory(str, Enum):
    MUSIC = "music"
    COMEDY = "comedy"
    TECH = "tech"
    FITNESS = "fitness"
    FOOD = "food"
    ART = "art"
    NETWORKING = "networking"
    GAMING = "gaming"
    POPUP = "popup"
    OTHER = "other"


class SocialVibe(str, Enum):
    CHILL = "chill"
    SOCIAL = "social"
    PROFESSIONAL = "professional"
    PARTY = "party"
    ACTIVE = "active"
    INTIMATE = "intimate"


class SourcePlatform(str, Enum):
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    WEBSITE = "website"
    GOOGLE_PLACES = "google_places"
    CURATOR = "curator"
    MANUAL = "manual"


class RawSignal(BaseModel):
    """
    Unprocessed content from any source — a post, flyer, link, or curator message.
    This is the input to the AI extraction layer.
    """

    id: Optional[str] = None
    source_platform: SourcePlatform
    source_url: Optional[str] = None
    raw_text: Optional[str] = None
    image_urls: list[str] = []
    fetched_at: datetime = datetime.utcnow()
    metadata: dict = {}


class ExtractedEvent(BaseModel):
    """
    Structured event data produced by the AI extractor from a RawSignal.
    Not yet validated or stored — may have missing/low-confidence fields.
    """

    name: str
    description: Optional[str] = None
    date_raw: Optional[str] = None          # e.g. "this Saturday", "May 17"
    date: Optional[datetime] = None         # resolved datetime if parseable
    location_raw: Optional[str] = None      # e.g. "Terra Kulture, VI"
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    cost_raw: Optional[str] = None          # e.g. "₦5,000 at door"
    cost_min: Optional[int] = None          # in NGN
    cost_max: Optional[int] = None          # in NGN, if range
    is_free: bool = False
    categories: list[EventCategory] = []
    vibe: Optional[SocialVibe] = None
    ticket_url: Optional[str] = None        # direct link to buy tickets / book
    is_scarce: bool = False                 # "limited spots", "almost sold out", etc.
    source_platform: SourcePlatform = SourcePlatform.MANUAL
    source_url: Optional[str] = None
    confidence: float = 0.0                 # 0.0–1.0, set by extractor
    needs_review: bool = True

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class StoredEvent(ExtractedEvent):
    """
    A fully validated, geocoded event ready for storage and delivery.
    """

    id: str
    created_at: datetime = datetime.utcnow()
    updated_at: datetime = datetime.utcnow()
    approved: bool = False
    active: bool = True
