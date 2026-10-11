-- HyLocal database schema
-- Run this in your Supabase SQL editor to set up the tables.

-- ─────────────────────────────────────────────
-- Events
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    description     TEXT,
    date_raw        TEXT,
    date            TIMESTAMPTZ,
    location_raw    TEXT,
    location_lat    FLOAT,
    location_lng    FLOAT,
    cost_raw        TEXT,
    cost_min        INTEGER,                  -- NGN
    cost_max        INTEGER,                  -- NGN
    is_free         BOOLEAN DEFAULT FALSE,
    categories      TEXT[] DEFAULT '{}',
    vibe            TEXT,
    source_platform TEXT,
    source_url      TEXT UNIQUE,
    confidence      FLOAT DEFAULT 0.0,
    needs_review    BOOLEAN DEFAULT TRUE,
    approved        BOOLEAN DEFAULT FALSE,
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS events_date_idx ON events (date);
CREATE INDEX IF NOT EXISTS events_approved_idx ON events (approved, active);

-- ─────────────────────────────────────────────
-- Users
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id     BIGINT UNIQUE NOT NULL,
    username        TEXT,
    first_name      TEXT,
    is_curator      BOOLEAN DEFAULT FALSE,
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- User preferences
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS preferences (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
    categories      TEXT[] DEFAULT '{}',       -- interests e.g. ['music', 'tech']
    max_budget      INTEGER,                   -- NGN, null = no limit
    neighbourhoods  TEXT[] DEFAULT '{}',       -- e.g. ['Ikeja', 'Ketu']
    vibes           TEXT[] DEFAULT '{}',       -- preferred social vibes
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id)
);

-- ─────────────────────────────────────────────
-- Feedback (thumbs up/down on recommendations)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feedback (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    event_id    UUID REFERENCES events(id) ON DELETE CASCADE,
    rating      SMALLINT CHECK (rating IN (-1, 1)),   -- -1 = dislike, 1 = like
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, event_id)
);

-- ─────────────────────────────────────────────
-- Auto-update updated_at on events
-- ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_updated_at
    BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
