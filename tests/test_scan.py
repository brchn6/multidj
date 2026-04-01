from multidj.scan import scan_library, format_scan


def test_scan_total_tracks(multidj_db):
    result = scan_library(str(multidj_db))
    assert result["summary"]["total_tracks"] == 9


def test_scan_excludes_deleted(multidj_db, multidj_db_conn):
    # soft-delete track 1, verify count drops
    multidj_db_conn.execute("UPDATE tracks SET deleted=1 WHERE id=1")
    multidj_db_conn.commit()
    result = scan_library(str(multidj_db))
    assert result["summary"]["total_tracks"] == 8


def test_scan_tracks_with_genre(multidj_db):
    # tracks 1,2,4,5,6,7,8 have genre (7 tracks). Track 3,9 have no genre.
    result = scan_library(str(multidj_db))
    assert result["summary"]["tracks_with_genre"] == 7


def test_scan_tracks_with_bpm(multidj_db):
    # all 9 active tracks have bpm
    result = scan_library(str(multidj_db))
    assert result["summary"]["tracks_with_bpm"] == 9


def test_scan_tracks_with_key(multidj_db):
    # tracks 1,4,6,8 have key (TRACK_KEY_IDS: 1->8B, 4->8B, 6->9A, 8->5A)
    result = scan_library(str(multidj_db))
    assert result["summary"]["tracks_with_key"] == 4


def test_scan_format_no_crash(multidj_db):
    result = scan_library(str(multidj_db))
    output = format_scan(result)
    assert "active tracks" in output


def test_scan_verbose_lists_tables(multidj_db):
    result = scan_library(str(multidj_db), verbose=True)
    assert "tables" in result
    assert "tracks" in result["tables"]
