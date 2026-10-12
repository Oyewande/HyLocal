"""
Extractor — AI-powered event data extraction from raw signals.

Supports both text-only and vision (flyer/image) extraction.
Provider chain: Anthropic Claude (primary) → OpenRouter (fallback).
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
from typing import Optional

import anthropic
import httpx
from loguru import logger
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from scraper.models import (
    EventCategory,
    ExtractedEvent,
    RawSignal,
    SocialVibe,
)

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "").strip()
OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL    = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free").strip()

# Anthropic status codes that mean "no credits / rate limited" — fall through to OpenRouter
FALLBACK_STATUS_CODES = {402, 429}

# Max images to send per signal (first N only — first image is usually the flyer)
MAX_IMAGES = 2
# Max image dimension (px) — resize above this to save tokens
MAX_IMAGE_DIM = 1568

EXTRACTION_PROMPT = """You are an event extraction assistant for a Lagos, Nigeria hyperlocal discovery app.

Extract structured event information from the content below. The content may be:
- A social media caption or post
- A FLYER IMAGE (very common in Lagos event culture — read all text visible in the image)
- Both text and a flyer image together

When an image is provided, carefully read ALL text in the image — event name, date, venue, price, and ticket links are often embedded in the flyer design. The image is often MORE informative than the caption.

Return ONLY a valid JSON object — no explanation, no markdown, no code fences. Just the raw JSON:
{
  "name": "Event name (string, required)",
  "description": "Brief description (string or null)",
  "date_raw": "Date as written in the post or flyer e.g. 'this Saturday', 'May 17', 'Sat Oct 19' (string or null)",
  "location_raw": "Location as written e.g. 'Terra Kulture, VI', 'Landmark Centre, Oniru' (string or null)",
  "cost_raw": "Cost as written e.g. '5000 at door', 'Free entry', '₦10k–₦25k' (string or null)",
  "cost_min": "Minimum cost in NGN as integer, 0 if free (integer or null)",
  "cost_max": "Maximum cost in NGN as integer if range given (integer or null)",
  "is_free": "true if free entry (boolean)",
  "categories": ["array from: music, comedy, tech, fitness, food, art, networking, gaming, popup, other"],
  "vibe": "one of: chill, social, professional, party, active, intimate (string or null)",
  "ticket_url": "Direct URL to buy tickets or make a reservation if explicitly shown in the post or flyer (string or null)",
  "is_scarce": "true if post or flyer mentions urgency like 'limited spots', 'almost sold out', 'few tickets left', 'selling fast' (boolean)",
  "confidence": "your confidence 0.0-1.0 that this is actually an event worth attending (float)"
}

Rules:
- If the content is NOT an event (generic post, ad, news, personal photo), set confidence below 0.3
- Lagos neighbourhoods: Ikeja, Ketu, Magodo, Berger, Yaba, Lekki, VI, Ikoyi, Surulere, Maryland, Gbagada
- NGN and the naira sign (₦) are the same currency. K means thousands (5k = 5000)
- If no date is mentioned, set date_raw to null
- name is required — infer from context if not stated (e.g. "Comedy Night")
- Flyer text often uses decorative fonts — read carefully

Content:
{CONTENT}"""


def _resize_image(img_bytes: bytes, max_dim: int = MAX_IMAGE_DIM) -> bytes:
    """
    Resize image so its longest side is at most max_dim pixels.
    Returns original bytes if already within limit or if Pillow isn't available.
    Always re-encodes as JPEG for consistent media_type.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        # Convert to RGB (handles RGBA PNGs, etc.)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()
    except ImportError:
        # Pillow not installed — send as-is
        return img_bytes
    except Exception as e:
        logger.debug(f"Image resize failed: {e} — sending original")
        return img_bytes


def _fetch_image_b64(url: str) -> Optional[tuple[str, str]]:
    """
    Download an image and return (base64_data, media_type).
    Returns None on failure.
    """
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "HyLocal/1.0"})
            resp.raise_for_status()
            raw = resp.content
            # Detect media type from Content-Type header
            ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if ct not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                ct = "image/jpeg"  # safe default for unknown types

        processed = _resize_image(raw)
        b64 = base64.standard_b64encode(processed).decode("utf-8")
        # After resize we always output JPEG
        media_type = "image/jpeg"
        logger.debug(f"Image fetched: {url} ({len(processed)//1024}KB)")
        return b64, media_type
    except Exception as e:
        logger.warning(f"Could not fetch image {url}: {e}")
        return None


class Extractor:
    """
    Extracts structured event data from raw signals using AI.

    Supports:
    - Text-only extraction (captions, posts)
    - Vision extraction (flyers passed as base64 images)
    - Combined text + image (most common: caption + flyer)

    Provider priority:
      1. Anthropic Claude Haiku (if ANTHROPIC_API_KEY is set and has credits)
      2. OpenRouter (if OPENROUTER_API_KEY is set) — default model: gemini-2.0-flash-exp:free

    Falls back automatically on credit/rate errors from Anthropic.
    """

    def __init__(self) -> None:
        self._anthropic: anthropic.Anthropic | None = None
        self._openrouter: OpenAI | None = None

        if ANTHROPIC_API_KEY:
            self._anthropic = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            logger.info("Extractor: Anthropic configured (primary)")

        if OPENROUTER_API_KEY:
            self._openrouter = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=OPENROUTER_API_KEY,
            )
            logger.info(f"Extractor: OpenRouter configured (fallback) — model: {OPENROUTER_MODEL}")

        if not self._anthropic and not self._openrouter:
            raise ValueError(
                "No AI provider configured. Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env"
            )

    def extract(
        self,
        signal: RawSignal,
        preloaded_images: list[bytes] | None = None,
    ) -> ExtractedEvent | None:
        """
        Extract a structured event from a RawSignal.

        preloaded_images: raw image bytes already in memory (e.g. downloaded
        directly from Telegram). When provided these are used instead of
        fetching signal.image_urls over the network.
        """
        content_parts: list[str] = []

        if signal.raw_text:
            content_parts.append(f"Text: {signal.raw_text}")
        if signal.metadata.get("location"):
            content_parts.append(f"Tagged location: {signal.metadata['location']}")
        if signal.metadata.get("hashtags"):
            content_parts.append(f"Hashtags: {', '.join(signal.metadata['hashtags'])}")

        has_text   = bool(content_parts)
        has_images = bool(preloaded_images or signal.image_urls)

        if not has_text and not has_images:
            logger.debug(f"Signal {signal.id} has no extractable content — skipping")
            return None

        content = "\n".join(content_parts) if content_parts else "(image only — see flyer)"

        # Build the images list from pre-loaded bytes or fetch from URLs
        images: list[tuple[str, str]] = []  # [(b64, media_type), ...]

        if preloaded_images:
            for raw in preloaded_images[:MAX_IMAGES]:
                processed = _resize_image(raw)
                b64 = base64.standard_b64encode(processed).decode("utf-8")
                images.append((b64, "image/jpeg"))
                logger.debug(f"Preloaded image encoded ({len(processed)//1024}KB)")
        else:
            for url in signal.image_urls[:MAX_IMAGES]:
                result = _fetch_image_b64(url)
                if result:
                    images.append(result)

        if has_images and not images:
            logger.debug(f"Signal {signal.id} had images but none could be loaded")

        try:
            extracted = self._call_with_fallback(content, signal, images)
            if extracted.confidence < 0.3:
                logger.debug(f"Signal {signal.id} confidence {extracted.confidence:.2f} too low")
                return None
            return extracted
        except Exception as e:
            logger.error(f"Extraction failed for signal {signal.id}: {e}")
            return None

    def _call_with_fallback(
        self,
        content: str,
        signal: RawSignal,
        images: list[tuple[str, str]],
    ) -> ExtractedEvent:
        prompt = EXTRACTION_PROMPT.replace("{CONTENT}", content)

        if self._anthropic:
            try:
                return self._call_anthropic(prompt, signal, images)
            except anthropic.APIStatusError as e:
                credit_error = e.status_code in FALLBACK_STATUS_CODES or (
                    e.status_code == 400 and "credit" in str(e).lower()
                )
                if credit_error:
                    logger.warning(
                        f"Anthropic unavailable ({e.status_code}) — falling back to OpenRouter"
                    )
                else:
                    raise
            except anthropic.APIError as e:
                logger.warning(f"Anthropic API error — falling back to OpenRouter: {e}")

        if self._openrouter:
            return self._call_openrouter(prompt, signal, images)

        raise RuntimeError("All AI providers exhausted")

    @retry(
        retry=retry_if_exception_type(anthropic.APIStatusError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=6),
        reraise=True,
    )
    def _call_anthropic(
        self,
        prompt: str,
        signal: RawSignal,
        images: list[tuple[str, str]],
    ) -> ExtractedEvent:
        # Build message content: images first (model reads flyer before text)
        content: list[dict] = []
        for b64, media_type in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64,
                },
            })
        content.append({"type": "text", "text": prompt})

        message = self._anthropic.messages.create(
            model="claude-haiku-5-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )
        raw = message.content[0].text.strip()
        logger.debug(f"[anthropic] {raw[:200]}")
        return self._build_event(self._parse_json(raw, signal, "anthropic"), signal)

    def _call_openrouter(
        self,
        prompt: str,
        signal: RawSignal,
        images: list[tuple[str, str]],
    ) -> ExtractedEvent:
        # OpenAI-compatible vision: content is a list with image_url and text items
        if images:
            content: list[dict] = []
            for b64, media_type in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{b64}"},
                })
            content.append({"type": "text", "text": prompt})
        else:
            content = prompt  # type: ignore[assignment]

        response = self._openrouter.chat.completions.create(
            model=OPENROUTER_MODEL,
            max_tokens=1024,
            timeout=30,
            messages=[{"role": "user", "content": content}],
        )
        text = response.choices[0].message.content
        if not text:
            raise ValueError(f"OpenRouter returned empty response (model: {OPENROUTER_MODEL})")
        raw = text.strip()
        logger.debug(f"[openrouter/{OPENROUTER_MODEL}] {raw[:200]}")
        return self._build_event(self._parse_json(raw, signal, "openrouter"), signal)

    def _parse_json(self, text: str, signal: RawSignal, provider: str = "") -> dict:
        # Strategy 1 — direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strategy 2 — strip code fence
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            try:
                return json.loads(fence.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Strategy 3 — first { ... } block
        brace = re.search(r"\{[\s\S]*\}", text)
        if brace:
            try:
                return json.loads(brace.group(0))
            except json.JSONDecodeError:
                pass

        logger.error(f"[{provider}] Could not parse JSON for signal {signal.id}.\nResponse: {text[:600]}")
        raise ValueError(f"No valid JSON in {provider} response")

    def _build_event(self, data: dict, signal: RawSignal) -> ExtractedEvent:
        return ExtractedEvent(
            name=data.get("name", "Unnamed Event"),
            description=data.get("description"),
            date_raw=data.get("date_raw"),
            location_raw=data.get("location_raw"),
            cost_raw=data.get("cost_raw"),
            cost_min=data.get("cost_min"),
            cost_max=data.get("cost_max"),
            is_free=data.get("is_free", False),
            categories=[
                EventCategory(c)
                for c in data.get("categories", [])
                if c in EventCategory._value2member_map_
            ],
            vibe=(
                SocialVibe(data["vibe"])
                if data.get("vibe") in SocialVibe._value2member_map_
                else None
            ),
            ticket_url=data.get("ticket_url"),
            is_scarce=bool(data.get("is_scarce", False)),
            source_platform=signal.source_platform,
            source_url=signal.source_url,
            confidence=float(data.get("confidence", 0.5)),
            needs_review=float(data.get("confidence", 0.5)) < 0.8,
        )
