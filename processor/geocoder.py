from __future__ import annotations

import os
from functools import lru_cache

import googlemaps
from loguru import logger

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# Manually maintained lookup for common Lagos venues.
# Checked first before hitting the Google Maps API — saves quota and handles
# venues with inconsistent or abbreviated address formats.
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

# Broad Lagos bounding box — used to validate geocoding results
# and avoid returning locations outside Lagos
LAGOS_BOUNDS = {
    "lat_min": 6.35,
    "lat_max": 6.70,
    "lng_min": 3.10,
    "lng_max": 3.70,
}


class Geocoder:
    """
    Resolves a raw location string to (lat, lng) coordinates.
    Strategy:
      1. Check the local venue lookup table
      2. Call Google Maps Geocoding API with Lagos bias
      3. Validate result is within Lagos bounds
    """

    def __init__(self) -> None:
        self._gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY) if GOOGLE_MAPS_API_KEY else None
        if not self._gmaps:
            logger.warning("GOOGLE_MAPS_API_KEY not set — geocoding will use venue lookup only")

    def geocode(self, location_raw: str) -> tuple[float, float] | None:
        """
        Returns (lat, lng) or None if the location can't be resolved.
        """
        if not location_raw:
            return None

        # 1. Try local lookup first
        normalized = location_raw.lower().strip()
        for venue, coords in VENUE_LOOKUP.items():
            if venue in normalized:
                logger.debug(f"Venue lookup hit for '{location_raw}' → {coords}")
                return coords

        # 2. Fall back to Google Maps
        if self._gmaps:
            return self._gmaps_geocode(location_raw)

        return None

    @lru_cache(maxsize=256)
    def _gmaps_geocode(self, location_raw: str) -> tuple[float, float] | None:
        try:
            results = self._gmaps.geocode(
                location_raw + ", Lagos, Nigeria",
                components={"country": "NG"},
            )
            if not results:
                logger.debug(f"No geocoding results for '{location_raw}'")
                return None

            loc = results[0]["geometry"]["location"]
            lat, lng = loc["lat"], loc["lng"]

            if not self._in_lagos(lat, lng):
                logger.warning(f"Geocoding result for '{location_raw}' is outside Lagos: ({lat}, {lng})")
                return None

            logger.debug(f"Geocoded '{location_raw}' → ({lat}, {lng})")
            return (lat, lng)

        except Exception as e:
            logger.error(f"Geocoding error for '{location_raw}': {e}")
            return None

    def _in_lagos(self, lat: float, lng: float) -> bool:
        b = LAGOS_BOUNDS
        return b["lat_min"] <= lat <= b["lat_max"] and b["lng_min"] <= lng <= b["lng_max"]
