from __future__ import annotations

import os
from functools import lru_cache

import httpx
from loguru import logger

GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY", "").strip()

GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"

# Manually maintained lookup for common Lagos venues.
# Checked first — saves API quota and handles abbreviated names.
VENUE_LOOKUP: dict[str, tuple[float, float]] = {
    "terra kulture": (6.4281, 3.4264),
    "cchub": (6.5006, 3.3896),
    "co-creation hub": (6.5006, 3.3896),
    "landmark centre": (6.4281, 3.4264),
    "eko hotel": (6.4264, 3.4264),
    "balmoral": (6.5244, 3.3792),
    "radisson blu": (6.4309, 3.4197),
    "genesis cinema": (6.4432, 3.4219),
    "filmhouse": (6.5547, 3.3792),
    "muri okunola park": (6.4333, 3.4213),
}

# Lagos bounding box — rejects results that fall outside
LAGOS_BOUNDS = {
    "lat_min": 6.35, "lat_max": 6.70,
    "lng_min": 3.10, "lng_max": 3.70,
}


class Geocoder:
    """
    Resolves a raw location string to (lat, lng).
    Strategy:
      1. Local venue lookup table (instant, free)
      2. Geoapify Geocoding API (free tier: 3,000/day)
    """

    def __init__(self) -> None:
        if not GEOAPIFY_API_KEY:
            logger.warning("GEOAPIFY_API_KEY not set — geocoding will use venue lookup only")

    def geocode(self, location_raw: str) -> tuple[float, float] | None:
        if not location_raw:
            return None

        # 1. Local lookup
        normalized = location_raw.lower().strip()
        for venue, coords in VENUE_LOOKUP.items():
            if venue in normalized:
                logger.debug(f"Venue lookup hit: '{location_raw}' → {coords}")
                return coords

        # 2. Geoapify
        if GEOAPIFY_API_KEY:
            return self._geoapify_geocode(location_raw)

        return None

    @lru_cache(maxsize=256)
    def _geoapify_geocode(self, location_raw: str) -> tuple[float, float] | None:
        try:
            response = httpx.get(
                GEOCODE_URL,
                params={
                    "text": f"{location_raw}, Lagos, Nigeria",
                    "apiKey": GEOAPIFY_API_KEY,
                    "limit": 1,
                    "filter": "countrycode:ng",
                    "bias": "proximity:3.3792,6.5244",  # biased toward Lagos centre
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            features = data.get("features", [])
            if not features:
                logger.debug(f"No geocoding results for '{location_raw}'")
                return None

            props = features[0]["geometry"]["coordinates"]
            lng, lat = props[0], props[1]  # GeoJSON is [lng, lat]

            if not self._in_lagos(lat, lng):
                logger.warning(f"Result for '{location_raw}' outside Lagos: ({lat}, {lng})")
                return None

            logger.debug(f"Geocoded '{location_raw}' → ({lat}, {lng})")
            return (lat, lng)

        except Exception as e:
            logger.error(f"Geocoding error for '{location_raw}': {e}")
            return None

    def _in_lagos(self, lat: float, lng: float) -> bool:
        b = LAGOS_BOUNDS
        return b["lat_min"] <= lat <= b["lat_max"] and b["lng_min"] <= lng <= b["lng_max"]
