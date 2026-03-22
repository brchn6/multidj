-- 001_initial.sql — MultiDJ full schema

CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY,
    path        TEXT    UNIQUE NOT NULL,
    artist      TEXT,
    title       TEXT,
    album       TEXT,
    genre       TEXT,
    bpm         REAL,
    key         TEXT,
    language    TEXT,
    duration    REAL,
    filesize    INTEGER,
    rating      INTEGER,
    play_count  INTEGER,
    remixer     TEXT,
    energy      REAL,
    intro_end   REAL,
    outro_start REAL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS track_tags (
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    key         TEXT    NOT NULL,
    value       TEXT,
    PRIMARY KEY (track_id, key)
);

CREATE TABLE IF NOT EXISTS crates (
    id      INTEGER PRIMARY KEY,
    name    TEXT    UNIQUE NOT NULL,
    type    TEXT    NOT NULL DEFAULT 'hand-curated',
    show    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS crate_tracks (
    crate_id    INTEGER NOT NULL REFERENCES crates(id)  ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id)  ON DELETE CASCADE,
    PRIMARY KEY (crate_id, track_id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    track_id        INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    adapter         TEXT    NOT NULL,
    last_synced_at  TEXT,
    dirty           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (track_id, adapter)
);

CREATE TRIGGER IF NOT EXISTS tracks_set_dirty
AFTER UPDATE ON tracks
FOR EACH ROW
BEGIN
    UPDATE sync_state SET dirty = 1 WHERE track_id = OLD.id;
END;
