CREATE TABLE IF NOT EXISTS embeddings (
    track_id    INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    model_name  TEXT    NOT NULL,
    vector      BLOB    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
