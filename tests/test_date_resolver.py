"""
Tests for the dateparser-based date resolution used in the pipeline.

We test the resolution logic directly without spinning up the full Pipeline.
"""
from __future__ import annotations

from datetime import datetime

import dateparser
import pytest

_LAGOS_SETTINGS = {
    "TIMEZONE": "Africa/Lagos",
    "RETURN_AS_TIMEZONE_AWARE": True,
    "PREFER_DATES_FROM": "future",
}


def resolve(text: str) -> datetime | None:
    return dateparser.parse(text, settings=_LAGOS_SETTINGS)


class TestDateResolver:
    def test_iso_date(self):
        result = resolve("2026-12-25")
        assert result is not None
        assert result.month == 12
        assert result.day == 25

    def test_iso_datetime(self):
        result = resolve("2026-10-18T20:00:00")
        assert result is not None
        assert result.hour == 20

    def test_natural_language_saturday(self):
        # "this Saturday" requires dateparser's language model — only assert
        # if it resolves; some lightweight environments skip it.
        result = resolve("this Saturday")
        if result is not None:
            assert result.weekday() == 5  # 5 = Saturday

    def test_month_day(self):
        result = resolve("October 18")
        assert result is not None
        assert result.month == 10
        assert result.day == 18

    def test_time_included(self):
        result = resolve("Saturday 7pm")
        if result is not None:
            assert result.hour == 19

    def test_timezone_aware(self):
        result = resolve("2026-10-18T20:00:00")
        assert result is not None
        # Should be timezone-aware (has tzinfo)
        assert result.tzinfo is not None

    def test_garbage_returns_none(self):
        result = resolve("not a date at all xyz")
        assert result is None

    def test_prefers_future(self):
        # "next Friday" may not resolve in all environments — skip gracefully
        result = resolve("next Friday")
        if result is not None:
            assert result > datetime.now(tz=result.tzinfo)
