from multidj.audit import audit_genres, audit_metadata


def test_audit_genres_case_collision(multidj_db):
    result = audit_genres(str(multidj_db))
    collisions = result["case_collisions"]
    # "House" and "house" are both present (tracks 1,6 vs track 4)
    normalized = [c["normalized"] for c in collisions]
    assert "house" in normalized


def test_audit_genres_uninformative(multidj_db):
    result = audit_genres(str(multidj_db))
    uninformative = [g["genre"] for g in result["uninformative_genres"]]
    assert "Music" in uninformative


def test_audit_genres_top_list(multidj_db):
    result = audit_genres(str(multidj_db))
    assert len(result["top_genres"]) > 0
    # House appears most (tracks 1,4,6 — but 4 is "house" lowercase, so case-distinct)
    genres = [g["genre"] for g in result["top_genres"]]
    assert "House" in genres


def test_audit_metadata_total(multidj_db):
    result = audit_metadata(str(multidj_db))
    assert result["total_tracks"] == 9


def test_audit_metadata_genre_coverage(multidj_db):
    result = audit_metadata(str(multidj_db))
    coverage = {c["field"]: c for c in result["coverage"]}
    # 7 tracks have genre out of 9
    assert coverage["genre"]["present"] == 7
    assert coverage["genre"]["missing"] == 2


def test_audit_metadata_bpm_coverage(multidj_db):
    result = audit_metadata(str(multidj_db))
    coverage = {c["field"]: c for c in result["coverage"]}
    assert coverage["bpm"]["present"] == 9
