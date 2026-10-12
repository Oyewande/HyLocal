"""
Tests for vision/image extraction helpers in processor/extractor.py

All tests are offline — no network calls, no API keys needed.
"""
from __future__ import annotations

import base64
import io
import sys
import types
from unittest.mock import MagicMock, patch, Mock

import pytest


# ── Stubs for missing packages in CI ─────────────────────────────────────────

def _stub_packages():
    for mod_name in ("anthropic", "openai"):
        if mod_name not in sys.modules:
            stub = types.ModuleType(mod_name)
            stub.Anthropic = MagicMock()
            stub.APIStatusError = Exception
            stub.APIError = Exception
            stub.OpenAI = MagicMock()
            sys.modules[mod_name] = stub

_stub_packages()
sys.modules.pop("processor.extractor", None)
from processor.extractor import _resize_image, _fetch_image_b64, MAX_IMAGE_DIM  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_jpeg(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal valid JPEG in memory."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color=(255, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_png_rgba(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal valid RGBA PNG in memory."""
    from PIL import Image
    img = Image.new("RGBA", (width, height), color=(100, 200, 50, 180))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── _resize_image tests ───────────────────────────────────────────────────────

class TestResizeImage:
    def test_small_image_not_upscaled(self):
        """Images smaller than MAX_IMAGE_DIM should not be enlarged."""
        from PIL import Image
        data = make_jpeg(200, 200)
        result = _resize_image(data, max_dim=MAX_IMAGE_DIM)
        img = Image.open(io.BytesIO(result))
        assert img.size[0] <= MAX_IMAGE_DIM
        assert img.size[1] <= MAX_IMAGE_DIM

    def test_large_image_resized(self):
        """Images larger than max_dim should be scaled down."""
        from PIL import Image
        data = make_jpeg(2000, 3000)
        result = _resize_image(data, max_dim=1000)
        img = Image.open(io.BytesIO(result))
        assert max(img.size) <= 1000

    def test_landscape_aspect_preserved(self):
        """Aspect ratio should be preserved when resizing a landscape image."""
        from PIL import Image
        data = make_jpeg(2000, 1000)  # 2:1 ratio
        result = _resize_image(data, max_dim=1000)
        img = Image.open(io.BytesIO(result))
        w, h = img.size
        assert w == 1000
        assert h == pytest.approx(500, abs=2)

    def test_portrait_aspect_preserved(self):
        """Aspect ratio should be preserved when resizing a portrait image."""
        from PIL import Image
        data = make_jpeg(1000, 2000)  # 1:2 ratio
        result = _resize_image(data, max_dim=1000)
        img = Image.open(io.BytesIO(result))
        w, h = img.size
        assert h == 1000
        assert w == pytest.approx(500, abs=2)

    def test_rgba_png_converted_to_rgb_jpeg(self):
        """RGBA PNGs (common in designed flyers) should be safely converted."""
        from PIL import Image
        data = make_png_rgba(100, 100)
        result = _resize_image(data)
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_output_is_jpeg(self):
        """Processed images should always be JPEG."""
        from PIL import Image
        data = make_jpeg(100, 100)
        result = _resize_image(data)
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"

    def test_returns_bytes(self):
        data = make_jpeg(100, 100)
        result = _resize_image(data)
        assert isinstance(result, bytes)
        assert len(result) > 0


# ── _fetch_image_b64 tests ────────────────────────────────────────────────────

class TestFetchImageB64:
    def test_successful_fetch_returns_tuple(self):
        """A successful fetch should return (base64_string, media_type)."""
        jpeg_bytes = make_jpeg(100, 100)
        mock_resp = Mock()
        mock_resp.content = jpeg_bytes
        mock_resp.headers = {"content-type": "image/jpeg"}
        mock_resp.raise_for_status = Mock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = mock_resp

            result = _fetch_image_b64("https://example.com/flyer.jpg")

        assert result is not None
        b64, media_type = result
        assert media_type == "image/jpeg"
        # Should be valid base64
        decoded = base64.b64decode(b64)
        assert len(decoded) > 0

    def test_failed_fetch_returns_none(self):
        """Network errors should return None without raising."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.side_effect = Exception("Connection refused")

            result = _fetch_image_b64("https://example.com/flyer.jpg")

        assert result is None

    def test_http_error_returns_none(self):
        """HTTP 4xx/5xx errors should return None without raising."""
        import httpx as _httpx
        mock_resp = Mock()
        mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
            "404", request=Mock(), response=Mock()
        )

        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = mock_resp

            result = _fetch_image_b64("https://example.com/missing.jpg")

        assert result is None

    def test_unknown_content_type_defaults_to_jpeg(self):
        """Unrecognised content-type headers should fall back to image/jpeg."""
        jpeg_bytes = make_jpeg(50, 50)
        mock_resp = Mock()
        mock_resp.content = jpeg_bytes
        mock_resp.headers = {"content-type": "application/octet-stream"}
        mock_resp.raise_for_status = Mock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = mock_resp

            result = _fetch_image_b64("https://example.com/flyer.bin")

        assert result is not None
        _, media_type = result
        assert media_type == "image/jpeg"
