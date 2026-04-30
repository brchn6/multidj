import sqlite3
from multidj.clean import clean_genres, clean_text


def _insert_text_track(multidj_db_conn, track_id: int, title: str, artist: str = "Test Artist", album: str = "Test Album"):
    multidj_db_conn.execute(
        """
        INSERT INTO tracks (id, path, artist, title, album, genre, bpm, deleted)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            track_id,
            f"/music/fixture/{track_id:03d}_test.mp3",
            artist,
            title,
            album,
            "House",
            128.0,
        ),
    )
    multidj_db_conn.commit()


def _insert_track(multidj_db_conn: sqlite3.Connection, path: str, genre: str) -> int:
    cur = multidj_db_conn.execute(
        "INSERT INTO tracks (path, genre, deleted) VALUES (?, ?, 0)",
        (path, genre),
    )
    multidj_db_conn.commit()
    return int(cur.lastrowid)


def test_clean_genres_detects_uninformative(multidj_db):
    result = clean_genres(str(multidj_db), apply=False)
    reasons = [c["reason"] for c in result["changes"]]
    assert "uninformative" in reasons


def test_clean_genres_detects_case_variant(multidj_db):
    result = clean_genres(str(multidj_db), apply=False)
    reasons = [c["reason"] for c in result["changes"]]
    assert "case_variant" in reasons


def test_clean_genres_detects_whitespace(multidj_db):
    result = clean_genres(str(multidj_db), apply=False)
    reasons = [c["reason"] for c in result["changes"]]
    assert "whitespace" in reasons


def test_clean_genres_dry_run(multidj_db, multidj_db_conn):
    before = multidj_db_conn.execute("SELECT genre FROM tracks ORDER BY id").fetchall()
    clean_genres(str(multidj_db), apply=False)
    after = multidj_db_conn.execute("SELECT genre FROM tracks ORDER BY id").fetchall()
    assert before == after


def test_clean_genres_apply_uninformative(multidj_db, multidj_db_conn):
    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id=7").fetchone()
    assert row["genre"] is None  # "Music" -> NULL


def test_clean_genres_apply_case(multidj_db, multidj_db_conn):
    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id=4").fetchone()
    assert row["genre"] == "House"  # "house" -> "House"


def test_clean_genres_apply_whitespace(multidj_db, multidj_db_conn):
    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id=6").fetchone()
    assert row["genre"] == "House"  # " House " -> "House"


def test_clean_genres_idempotent(multidj_db):
    r1 = clean_genres(str(multidj_db), apply=True, backup=False)
    r2 = clean_genres(str(multidj_db), apply=True, backup=False)
    assert r2["total_changes"] == 0


def test_clean_text_dry_run(multidj_db, multidj_db_conn):
    before = multidj_db_conn.execute("SELECT artist FROM tracks ORDER BY id").fetchall()
    clean_text(str(multidj_db), apply=False)
    after = multidj_db_conn.execute("SELECT artist FROM tracks ORDER BY id").fetchall()
    assert before == after


def test_clean_modes(multidj_db):
    r = clean_genres(str(multidj_db), apply=False)
    assert r["mode"] == "dry_run"
    r2 = clean_genres(str(multidj_db), apply=True, backup=False)
    assert r2["mode"] == "apply"


def test_clean_text_aggressive_dry_run_no_write(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 101, "Song Name (Official Lyric Video)")
    before = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=101").fetchone()
    clean_text(str(multidj_db), apply=False)
    after = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=101").fetchone()
    assert before["title"] == after["title"]


def test_clean_text_aggressive_apply_writes(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 102, "Song Name (Official Video)")
    clean_text(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=102").fetchone()
    assert row["title"] == "Song Name"


def test_clean_text_aggressive_removes_known_video_noise(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 103, "Artist - Song Name [Official Video]")
    _insert_text_track(multidj_db_conn, 104, "Song Name {Official Audio}")
    _insert_text_track(multidj_db_conn, 105, "Song Name (Lyrics)")

    clean_text(str(multidj_db), apply=True, backup=False)

    row1 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=103").fetchone()
    row2 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=104").fetchone()
    row3 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=105").fetchone()
    assert row1["title"] == "Artist - Song Name"
    assert row2["title"] == "Song Name"
    assert row3["title"] == "Song Name"


def test_clean_text_aggressive_preserves_meaningful_descriptors(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 106, "Song Name (feat. Artist)")
    _insert_text_track(multidj_db_conn, 107, "Song Name (Remix)")
    _insert_text_track(multidj_db_conn, 108, "Song Name (Extended Mix)")

    clean_text(str(multidj_db), apply=True, backup=False)

    row1 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=106").fetchone()
    row2 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=107").fetchone()
    row3 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=108").fetchone()
    assert row1["title"] == "Song Name (feat. Artist)"
    assert row2["title"] == "Song Name (Remix)"
    assert row3["title"] == "Song Name (Extended Mix)"


def test_clean_text_aggressive_whitespace_still_works(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 109, "  Song Name (Official Audio)  ")
    clean_text(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=109").fetchone()
    assert row["title"] == "Song Name"


def test_clean_text_removes_unbracketed_official_suffix(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 110, "ABBA - Dancing Queen Official Lyric Video")
    clean_text(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=110").fetchone()
    assert row["title"] == "ABBA - Dancing Queen"


def test_clean_text_removes_mapped_free_and_bpm_markers(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 111, "Track Name [FREE D_L]")
    _insert_text_track(multidj_db_conn, 112, "Track Name [136 BPM]")
    clean_text(str(multidj_db), apply=True, backup=False)
    row1 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=111").fetchone()
    row2 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=112").fetchone()
    assert row1["title"] == "Track Name"
    assert row2["title"] == "Track Name"


def test_clean_text_removes_single_version_and_remaster_markers(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 113, "Hot Stuff (Single Version)")
    _insert_text_track(multidj_db_conn, 114, "Le Freak (2018 Remaster)")
    _insert_text_track(multidj_db_conn, 115, "Paul Johnson - Get Get Down - REMASTERED")
    clean_text(str(multidj_db), apply=True, backup=False)
    row1 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=113").fetchone()
    row2 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=114").fetchone()
    row3 = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=115").fetchone()
    assert row1["title"] == "Hot Stuff"
    assert row2["title"] == "Le Freak"
    assert row3["title"] == "Paul Johnson - Get Get Down"


def test_clean_text_removes_numa_edit_when_requested(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 116, "DUDA (Numa Edit)")
    clean_text(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT title FROM tracks WHERE id=116").fetchone()
    assert row["title"] == "DUDA"


def test_clean_text_strips_leading_artist_00_only(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 117, "Track Name", artist="00 ABBA")
    _insert_text_track(multidj_db_conn, 118, "Track Name", artist="DJBOUNTY3000")
    clean_text(str(multidj_db), apply=True, backup=False)
    row1 = multidj_db_conn.execute("SELECT artist FROM tracks WHERE id=117").fetchone()
    row2 = multidj_db_conn.execute("SELECT artist FROM tracks WHERE id=118").fetchone()
    assert row1["artist"] == "ABBA"
    assert row2["artist"] == "DJBOUNTY3000"


def test_clean_text_removes_artist_trailing_free_dl_download(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 119, "Track Name", artist="Some Artist - FREE")
    _insert_text_track(multidj_db_conn, 120, "Track Name", artist="Another Artist (DL)")
    _insert_text_track(multidj_db_conn, 121, "Track Name", artist="Third Artist : download")

    clean_text(str(multidj_db), apply=True, backup=False)

    row1 = multidj_db_conn.execute("SELECT artist FROM tracks WHERE id=119").fetchone()
    row2 = multidj_db_conn.execute("SELECT artist FROM tracks WHERE id=120").fetchone()
    row3 = multidj_db_conn.execute("SELECT artist FROM tracks WHERE id=121").fetchone()
    assert row1["artist"] == "Some Artist"
    assert row2["artist"] == "Another Artist"
    assert row3["artist"] == "Third Artist"


def test_clean_text_removes_artist_leading_free_download_prefix(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 122, "Track Name", artist="FREE DOWNLOAD: Artist Name")
    _insert_text_track(multidj_db_conn, 123, "Track Name", artist="(FREE DL) : Another Artist")

    clean_text(str(multidj_db), apply=True, backup=False)

    row1 = multidj_db_conn.execute("SELECT artist FROM tracks WHERE id=122").fetchone()
    row2 = multidj_db_conn.execute("SELECT artist FROM tracks WHERE id=123").fetchone()
    assert row1["artist"] == "Artist Name"
    assert row2["artist"] == "Another Artist"


def test_clean_text_removes_artist_inline_free_dl_tokens(multidj_db, multidj_db_conn):
    _insert_text_track(multidj_db_conn, 124, "Track Name", artist="A Free Artist DL")

    clean_text(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT artist FROM tracks WHERE id=124").fetchone()
    assert row["artist"] == "A Artist"


def test_clean_genres_apply_donk_canonical_case(multidj_db, multidj_db_conn):
    _insert_track(multidj_db_conn, "/music/fixture/11_donk_upper.mp3", "Donk")
    track_id = _insert_track(multidj_db_conn, "/music/fixture/12_donk_lower.mp3", "DONK")

    clean_genres(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] == "Donk"


def test_clean_genres_nulls_hebrew_metadata_junk(multidj_db, multidj_db_conn):
    genre = "sarit hadad, meortz hahaim, מירוץ החיים, רמיקס"
    track_id = _insert_track(multidj_db_conn, "/music/fixture/13_hebrew_junk.mp3", genre)

    result = clean_genres(str(multidj_db), apply=False)
    change = next(c for c in result["changes"] if c["track_id"] == track_id)
    assert change["new_genre"] is None
    assert change["reason"] == "hebrew_metadata_junk"

    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] is None


def test_clean_genres_nulls_long_multi_value_junk(multidj_db, multidj_db_conn):
    genre = "Free Download, UKG, NUKG, UK Garage, Garage"
    track_id = _insert_track(multidj_db_conn, "/music/fixture/14_multi_junk.mp3", genre)

    result = clean_genres(str(multidj_db), apply=False)
    change = next(c for c in result["changes"] if c["track_id"] == track_id)
    assert change["new_genre"] is None
    assert change["reason"] in {"suspicious_multi_value", "junk_token"}

    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] is None


def test_clean_genres_keeps_uk_garage(multidj_db, multidj_db_conn):
    track_id = _insert_track(multidj_db_conn, "/music/fixture/15_uk_garage.mp3", "UK Garage")

    clean_genres(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] == "UK Garage"


def test_clean_genres_keeps_drum_and_bass(multidj_db, multidj_db_conn):
    track_id = _insert_track(multidj_db_conn, "/music/fixture/16_dnb.mp3", "Drum & Bass")

    clean_genres(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] == "Drum & Bass"


def test_clean_genres_keeps_afro_house(multidj_db, multidj_db_conn):
    track_id = _insert_track(multidj_db_conn, "/music/fixture/17_afro_house.mp3", "Afro House")

    clean_genres(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] == "Afro House"


def test_clean_genres_nulls_symbol_only(multidj_db, multidj_db_conn):
    track_id = _insert_track(multidj_db_conn, "/music/fixture/18_symbol_only.mp3", ":)")

    result = clean_genres(str(multidj_db), apply=False)
    change = next(c for c in result["changes"] if c["track_id"] == track_id)
    assert change["new_genre"] is None
    assert change["reason"] == "symbol_only"

    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] is None
