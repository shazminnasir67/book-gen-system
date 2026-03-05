-- ============================================================
-- schema.sql — Automated Book Generation System
-- Run this in Supabase SQL Editor to initialize the database
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── books ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS books (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title                       TEXT NOT NULL,
  notes_on_outline_before     TEXT,
  outline                     TEXT,
  notes_on_outline_after      TEXT,
  status_outline_notes        TEXT CHECK (status_outline_notes IN ('yes', 'no', 'no_notes_needed')),
  outline_version             INT DEFAULT 1,
  chapter_notes_status        TEXT CHECK (chapter_notes_status IN ('yes', 'no', 'no_notes_needed')),
  final_review_notes_status   TEXT CHECK (final_review_notes_status IN ('yes', 'no', 'no_notes_needed')),
  book_output_status          TEXT DEFAULT 'pending',
  current_stage               TEXT DEFAULT 'awaiting_input',
  total_chapters              INT DEFAULT 0,
  created_at                  TIMESTAMPTZ DEFAULT now(),
  updated_at                  TIMESTAMPTZ DEFAULT now()
);

-- ── chapters ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chapters (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id         UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_number  INT NOT NULL,
  title           TEXT,
  content         TEXT,
  summary         TEXT,
  chapter_notes   TEXT,
  status          TEXT DEFAULT 'pending',
  version         INT DEFAULT 1,
  created_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE(book_id, chapter_number, version)
);

-- ── outline_versions ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS outline_versions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id     UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  version     INT NOT NULL,
  outline     TEXT NOT NULL,
  notes_used  TEXT,
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE(book_id, version)
);

-- ── notification_log ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notification_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id     UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  event_type  TEXT NOT NULL,
  channel     TEXT CHECK (channel IN ('email', 'teams', 'both')) NOT NULL,
  payload     JSONB,
  sent_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notification_book_id   ON notification_log(book_id);
CREATE INDEX IF NOT EXISTS idx_notification_event     ON notification_log(event_type);
CREATE INDEX IF NOT EXISTS idx_notification_sent_at   ON notification_log(sent_at DESC);

-- ── auto-update updated_at on books ───────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER books_updated_at
  BEFORE UPDATE ON books
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── indexes ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_chapters_book_id        ON chapters(book_id);
CREATE INDEX IF NOT EXISTS idx_chapters_number         ON chapters(book_id, chapter_number);
CREATE INDEX IF NOT EXISTS idx_outlines_book_id        ON outline_versions(book_id);
CREATE INDEX IF NOT EXISTS idx_books_current_stage     ON books(current_stage);
CREATE INDEX IF NOT EXISTS idx_books_output_status     ON books(book_output_status);
