-- 006_enrichment.sql — Phase 8: metadata enrichment columns
ALTER TABLE tracks ADD COLUMN release_year INTEGER;
ALTER TABLE tracks ADD COLUMN label TEXT;
