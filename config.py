"""
HyLocal — configuration & startup validation.

Call validate_env() at the top of main.py before anything else.
Fails immediately with a clear message if required vars are missing,
rather than crashing silently later during a scrape or digest run.
"""

from __future__ import annotations

import os
import sys

# ── Required vars ─────────────────────────────────────────────────────────────

REQUIRED: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN":  "Telegram bot token from @BotFather",
    "SUPABASE_URL":        "Your Supabase project URL  (Settings → API → Project URL)",
    "SUPABASE_KEY":        "Supabase service_role key  (Settings → API → service_role)",
}

# ── Optional vars (warn if missing but don't abort) ───────────────────────────

OPTIONAL: dict[str, str] = {
    "ANTHROPIC_API_KEY":       "Anthropic API key (primary AI provider)",
    "OPENROUTER_API_KEY":      "OpenRouter API key (fallback AI provider)",
    "GEOAPIFY_API_KEY":        "Geoapify key for geocoding venue names",
    "TELEGRAM_CURATOR_GROUP_ID": "Chat ID of your curator Telegram group",
    "OPENROUTER_MODEL":        "OpenRouter model (default: google/gemini-2.0-flash-exp:free)",
}


def validate_env() -> None:
    """
    Check all required env vars are present.
    Also warn about optional vars that are unset.
    Calls sys.exit(1) on any missing required var.
    """
    missing_required: list[str] = []
    for var, description in REQUIRED.items():
        if not os.getenv(var, "").strip():
            missing_required.append(f"  {var}\n    → {description}")

    if missing_required:
        lines = [
            "",
            "╔══════════════════════════════════════════════════════════════╗",
            "║  HyLocal — missing required environment variables            ║",
            "╚══════════════════════════════════════════════════════════════╝",
            "",
            "The following variables must be set in your .env file:",
            "",
            *missing_required,
            "",
            "Copy .env.example to .env and fill in your values:",
            "  cp .env.example .env",
            "",
        ]
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)

    # Warn about optional vars
    missing_optional = [v for v in OPTIONAL if not os.getenv(v, "").strip()]
    if missing_optional:
        # Check at least one AI provider is configured
        has_anthropic  = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
        has_openrouter = bool(os.getenv("OPENROUTER_API_KEY", "").strip())
        if not has_anthropic and not has_openrouter:
            print(
                "\n⚠️  WARNING: Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY is set.\n"
                "   AI extraction will not work. Set at least one in your .env file.\n",
                file=sys.stderr,
            )
        elif missing_optional:
            unset = ", ".join(missing_optional)
            print(f"ℹ️  Optional vars not set (features may be limited): {unset}", file=sys.stderr)
