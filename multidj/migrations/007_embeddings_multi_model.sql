-- Migration 007: Change embeddings table primary key from track_id to (track_id, model_name)
-- so that multiple embedding models can be stored per track (e.g. CLAP + CLaMP3).
--
-- SQLite does not support ALTER TABLE to change a primary key, so we recreate the table.

CREATE TABLE IF NOT EXISTS embeddings_new (
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    model_name  TEXT    NOT NULL,
    vector      BLOB    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (track_id, model_name)
);

INSERT OR IGNORE INTO embeddings_new (track_id, model_name, vector, created_at)
SELECT track_id, model_name, vector, created_at FROM embeddings;

DROP TABLE embeddings;

ALTER TABLE embeddings_new RENAME TO embeddings;
