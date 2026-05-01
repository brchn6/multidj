-- 003_crates_add_type.sql
-- Add MultiDJ `type` column to crates.
-- Required when crates table was pre-existing (e.g. from Mixxx) and
-- migration 001's CREATE TABLE IF NOT EXISTS skipped recreating it.
ALTER TABLE crates ADD COLUMN type TEXT NOT NULL DEFAULT 'hand-curated';
