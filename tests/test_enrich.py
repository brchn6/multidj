from multidj.enrich import enrich_language


def test_enrich_hebrew_count(multidj_db):
    result = enrich_language(str(multidj_db))
    assert result["hebrew_tracks"] == 1


def test_enrich_hebrew_track_id(multidj_db):
    result = enrich_language(str(multidj_db))
    track_ids = [t["track_id"] for t in result["tracks"]]
    assert 5 in track_ids  # track 5 is the Hebrew track


def test_enrich_total_tracks(multidj_db):
    result = enrich_language(str(multidj_db))
    assert result["total_active_tracks"] == 9


def test_enrich_no_false_positives(multidj_db):
    result = enrich_language(str(multidj_db))
    track_ids = [t["track_id"] for t in result["tracks"]]
    assert 1 not in track_ids  # "DJ Tiesto" is not Hebrew
