"""
Ground-truth fixture data for all MultiDJ tests.

This module is the single source of truth for fixture assertions across
all sub-agents (Layer 2+). Never modify values here without updating
every test that references them.
"""

from __future__ import annotations

# fmt: off
# Each tuple: (id, artist, title, genre, bpm, key_text, rating, timesplayed,
#               path, filesize, duration)
TRACKS = [
    (1,  "DJ Tiesto",        "Red Lights",                "House",  128.0, "8B", 4, 12,
     "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3",                 8500000, 240.0),
    (2,  "Armin van Buuren", "Blue Fear",                 "Trance", 138.0, None, 3,  5,
     "/music/fixture/02_Armin_van_Buuren_-_Blue_Fear.mp3",           9200000, 310.0),
    (3,  None,               None,                        None,     140.0, None, 0,  0,
     "/music/fixture/03_DJ_Tiesto_-_Red_Lights_Remix.mp3",           7800000, 215.0),
    (4,  "DJ Tiesto",        "Red Lights",                "house",  128.0, "8B", 2,  1,
     "/music/fixture/04_DJ_Tiesto_-_Red_Lights_v2.mp3",              7900000, 240.0),
    (5,  "עידן רייכל",       "מים",                       "Hebrew", 120.0, None, 0,  0,
     "/music/fixture/05_hebrew_track.mp3",                           6500000, 195.0),
    (6,  "Carl Cox",         "Pressure",                  " House ",136.0, "9A", 5, 20,
     "/music/fixture/06_Carl_Cox_-_Pressure.mp3",                   11000000, 420.0),
    (7,  "Miss Kittin",      "Frank Sinatra",             "Music",  130.0, None, 0,  0,
     "/music/fixture/07_Miss_Kittin_-_Frank_Sinatra.mp3",            8100000, 280.0),
    (8,  "Surgeon",          "Force + Form",              "Techno", 145.0, "5A", 3,  8,
     "/music/fixture/08_Surgeon_-_Force_Form.mp3",                   9800000, 350.0),
    (9,  "Laurent Garnier",  "The Man with the Red Face", None,     124.0, None, 0,  2,
     "/music/fixture/09_Laurent_Garnier_-_The_Man.mp3",             10200000, 480.0),
    # soft-deleted — mixxx_deleted=1, never counted
    (10, "Deleted Artist",   "Deleted Track",             "House",  130.0, None, 0,  0,
     "/music/fixture/10_deleted.mp3",                                5000000, 200.0),
]
# fmt: on

# Active track count (excludes id=10 which is soft-deleted)
ACTIVE_TRACK_COUNT = 9

CRATES = [
    # (id, name, type, show)
    (1, "Genre: House",  "auto",          1),
    (2, "My Favorites",  "hand-curated",  1),
    (3, "New Crate",     "catch-all",     1),
]

CRATE_TRACKS = [
    # (crate_id, track_id)
    (1, 1), (1, 4), (1, 6),   # Genre: House -> tracks 1, 4, 6
    (2, 1), (2, 8),            # My Favorites -> tracks 1, 8
    # New Crate is empty
]

# Keys table for Mixxx (key_id -> Camelot string)
KEYS = [
    (1, "8B"),
    (2, "9A"),
    (3, "5A"),
]

# track_id -> key_id mapping (None means no key assigned)
TRACK_KEY_IDS: dict[int, int] = {1: 1, 4: 1, 6: 2, 8: 3}
