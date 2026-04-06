-- 002_cue_points.sql — Cue point markers per track

CREATE TABLE IF NOT EXISTS cue_points (
    id          INTEGER PRIMARY KEY,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    type        TEXT    NOT NULL,   -- 'intro_end' | 'drop' | 'outro_start' | 'hot_cue'
    position    REAL    NOT NULL,   -- seconds from start of track
    label       TEXT,               -- optional display label
    color       INTEGER,            -- RGB integer for Mixxx hot cue color (NULL = default)
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_cue_points_track_id ON cue_points(track_id);
