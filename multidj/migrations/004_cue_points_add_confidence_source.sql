-- 004_cue_points_add_confidence_source.sql
-- Add confidence and source columns to cue_points for auto-detected cues.
ALTER TABLE cue_points ADD COLUMN confidence TEXT NOT NULL DEFAULT 'high';
ALTER TABLE cue_points ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
