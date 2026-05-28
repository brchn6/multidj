# Cue Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically detect musical structure (intro, verse, chorus, drop, outro, etc.) for every track and store as cue points, with the three DJ-critical cues (intro, drop, outro) synced to Mixxx as hot cues.

**Architecture:** `allin1` neural model does primary structural segmentation; `librosa` independently detects energy/spectral transitions; boundaries where both agree are marked `confidence='high'` and pushed to Mixxx. All other segments are stored in MultiDJ only. The feature is a pipeline step inserted after `energy` (step 7) as step 8.

**Tech Stack:** `allin1` (transformer structural segmentation), `librosa` (energy/spectral analysis), SQLite migrations, argparse CLI wiring.

**Spec:** `docs/superpowers/specs/2026-05-28-cue-detection-design.md`
(on `feat/embeddings-clustering` branch — access via `git show feat/embeddings-clustering:docs/superpowers/specs/2026-05-28-cue-detection-design.md` if branching from `dev`)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `multidj/migrations/005_cue_points_v2.sql` | Add `confidence` + `source` columns to `cue_points` |
| Create | `multidj/cues.py` | All cue detection + management logic |
| Modify | `pyproject.toml` | Add `allin1` + `torchaudio` to `embeddings` extra |
| Modify | `multidj/cli.py` | Add `analyze cues` subcommand + `cues clear` command group |
| Modify | `multidj/pipeline.py` | Insert `cues` as step 8 (after energy, before embed) |
| Modify | `multidj/adapters/mixxx.py` | Add `_push_cues_to_mixxx()` + call from `full_sync` |
| Modify | `tests/fixtures/mixxx_factory.py` | Add `cues` table to Mixxx DDL |
| Replace | `tests/test_analyze_cues.py` | Full test suite for `analyze_cues` + `clear_cues` |
| Create | `tests/test_mixxx_cue_sync.py` | Tests for Mixxx cue push |
| Modify | `tests/test_pipeline.py` | Assert cues step present + --skip-cues works |
| Modify | `CLAUDE.md` | Update commands table + pipeline step list |

---

## Task 0: Branch setup

- [ ] **Step 1: Create feature branch from dev**

```bash
git checkout dev
git pull origin dev
git checkout -b feat/cue-detection
```

Expected: you are now on `feat/cue-detection` branched from current `dev`.

---

## Task 1: Schema migration

**Files:**
- Create: `multidj/migrations/005_cue_points_v2.sql`

- [ ] **Step 1: Create the migration file**

```sql
-- 005_cue_points_v2.sql — Add confidence + source columns to cue_points

ALTER TABLE cue_points ADD COLUMN confidence TEXT NOT NULL DEFAULT 'high';
-- 'high' = allin1 + librosa both detected transition | 'low' = allin1 only

ALTER TABLE cue_points ADD COLUMN source TEXT NOT NULL DEFAULT 'auto';
-- 'auto' = written by analyze cues | 'manual' = user-set, never overwritten by analyze
```

- [ ] **Step 2: Verify migration applies cleanly**

```bash
python - <<'EOF'
import sqlite3, tempfile, os
from multidj.db import connect
with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
    path = f.name
with connect(path, readonly=False) as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cue_points)").fetchall()]
    assert "confidence" in cols, f"missing confidence, got: {cols}"
    assert "source" in cols, f"missing source, got: {cols}"
    print("OK — columns present:", cols)
os.unlink(path)
EOF
```

Expected output: `OK — columns present: [... 'confidence', 'source']`

- [ ] **Step 3: Commit**

```bash
git add multidj/migrations/005_cue_points_v2.sql
git commit -m "feat: add confidence + source columns to cue_points (migration 005)"
```

---

## Task 2: Add allin1 dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add allin1 and torchaudio to the embeddings extra**

In `pyproject.toml`, update the `embeddings` section:

```toml
[project.optional-dependencies]
analysis = [
    "librosa>=0.11.0",
    "mutagen>=1.47",
]
embeddings = [
    "torch>=2.0",
    "torchaudio>=2.0",
    "transformers>=4.40",
    "librosa>=0.11.0",
    "umap-learn>=0.5",
    "hdbscan>=0.8",
    "openai>=1.0",
    "allin1>=0.1.0",
]
```

- [ ] **Step 2: Sync and verify allin1 resolves**

```bash
uv sync --extra embeddings
python -c "import allin1; print('allin1 OK:', allin1.__version__)"
```

Expected: prints `allin1 OK:` followed by a version string. No import errors.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add allin1 + torchaudio to embeddings extra"
```

---

## Task 3: Core detection function

**Files:**
- Create: `multidj/cues.py`
- Test: `tests/test_analyze_cues.py` (replace existing stub)

- [ ] **Step 1: Replace the test stub with a test for detect_cues**

Write `tests/test_analyze_cues.py` (replace the entire file):

```python
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.multidj_factory import make_multidj_db


@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_allin1_result(segments=None, downbeats=None):
    """Build a mock allin1 analysis result."""
    if segments is None:
        segments = [
            MagicMock(start=0.0,   end=30.0,  label="intro"),
            MagicMock(start=30.0,  end=90.0,  label="verse"),
            MagicMock(start=90.0,  end=150.0, label="chorus"),
            MagicMock(start=150.0, end=180.0, label="outro"),
        ]
    if downbeats is None:
        # Downbeats every 2 seconds (simplistic)
        downbeats = [float(i * 2) for i in range(100)]
    result = MagicMock()
    result.segments = segments
    result.downbeats = downbeats
    return result


def _librosa_no_transitions():
    """Patch librosa so it finds no transitions (forces all cues low-confidence)."""
    import numpy as np
    # peak_pick returns empty array when signal is flat
    return np.zeros(500, dtype=float)


def _librosa_transitions_at(*times):
    """Patch librosa combined signal so peaks appear near given times."""
    import numpy as np
    arr = np.zeros(500, dtype=float)
    sr, hop = 22050, 512
    for t in times:
        idx = int(t * sr / hop)
        if 0 <= idx < len(arr):
            arr[idx] = 1.0
    return arr


# ---------------------------------------------------------------------------
# detect_cues — unit tests
# ---------------------------------------------------------------------------

def test_detect_cues_returns_list_of_dicts(tmp_path):
    from multidj.cues import detect_cues

    allin1_result = _make_allin1_result()

    import numpy as np

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        # librosa.load returns (zeros, 22050)
        mock_lr.load.return_value = (np.zeros(22050 * 10), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.linspace(0, 10, 500)
        mock_lr.util.peak_pick.return_value = np.array([], dtype=int)

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    assert isinstance(cues, list)
    assert len(cues) > 0
    for c in cues:
        assert "type" in c
        assert "position" in c
        assert "confidence" in c
        assert "label" in c


def test_detect_cues_high_confidence_when_librosa_agrees(tmp_path):
    from multidj.cues import detect_cues
    import numpy as np

    allin1_result = _make_allin1_result()

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 200), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        # Return times that align with segment starts (0, 30, 90, 150)
        mock_lr.times_like.return_value = np.array([0.0, 30.0, 90.0, 150.0] + [200.0] * 496)
        # Peaks at indices 0,1,2,3 → aligns with all segment starts
        mock_lr.util.peak_pick.return_value = np.array([0, 1, 2, 3])

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    high = [c for c in cues if c["confidence"] == "high"]
    assert len(high) >= 3  # at least intro, chorus/drop, outro are high confidence


def test_detect_cues_low_confidence_when_librosa_disagrees(tmp_path):
    from multidj.cues import detect_cues
    import numpy as np

    allin1_result = _make_allin1_result()

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 200), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.linspace(0, 200, 500)
        # No peaks at all — librosa finds nothing
        mock_lr.util.peak_pick.return_value = np.array([], dtype=int)

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    low = [c for c in cues if c["confidence"] == "low"]
    assert len(low) > 0


def test_detect_cues_derives_drop_from_chorus():
    from multidj.cues import detect_cues
    import numpy as np

    allin1_result = _make_allin1_result()

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 200), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.linspace(0, 200, 500)
        mock_lr.util.peak_pick.return_value = np.array([], dtype=int)

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    types = [c["type"] for c in cues]
    assert "drop" in types


def test_detect_cues_snaps_to_nearest_downbeat():
    from multidj.cues import detect_cues
    import numpy as np

    # Segment starts at 30.7s; nearest downbeat is 30.0
    downbeats = [0.0, 2.0, 4.0, 28.0, 30.0, 32.0, 90.0, 150.0]
    segs = [
        MagicMock(start=0.0,  end=30.7,  label="intro"),
        MagicMock(start=30.7, end=90.2,  label="verse"),
        MagicMock(start=90.2, end=150.1, label="chorus"),
        MagicMock(start=150.1,end=180.0, label="outro"),
    ]
    allin1_result = _make_allin1_result(segments=segs, downbeats=downbeats)

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 200), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.linspace(0, 200, 500)
        mock_lr.util.peak_pick.return_value = np.array([], dtype=int)

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    verse_cue = next(c for c in cues if c["type"] == "verse")
    assert verse_cue["position"] == pytest.approx(30.0)  # snapped from 30.7


def test_detect_cues_raises_on_missing_dep():
    import sys
    # Temporarily hide allin1
    saved = sys.modules.pop("allin1", None)
    try:
        # Reload cues to pick up missing import
        import importlib
        import multidj.cues as cues_mod
        importlib.reload(cues_mod)
        with pytest.raises(RuntimeError, match="embeddings"):
            cues_mod.detect_cues("/fake/track.mp3", bpm=128.0)
    finally:
        if saved is not None:
            sys.modules["allin1"] = saved
```

- [ ] **Step 2: Run the tests — confirm they all fail (module doesn't exist yet)**

```bash
.venv/bin/pytest tests/test_analyze_cues.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'multidj.cues'` or similar — tests fail because `cues.py` doesn't exist.

- [ ] **Step 3: Create multidj/cues.py with detect_cues()**

```python
from __future__ import annotations

import os
import sys
from typing import Any


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)


def _suppress_decoder_noise():
    """Context manager: redirect stderr to /dev/null during audio decode."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull, 2)
        os.close(devnull)
        try:
            yield
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)

    return _ctx()


try:
    import allin1  # type: ignore  # noqa: F401
    import librosa  # type: ignore  # noqa: F401
    import numpy as np  # type: ignore  # noqa: F401
except ImportError:
    allin1 = None  # type: ignore
    librosa = None  # type: ignore
    np = None  # type: ignore


# Maps allin1 segment labels to our cue_points.type enum values.
_LABEL_MAP: dict[str, str] = {
    "intro":        "intro",
    "verse":        "verse",
    "pre-chorus":   "pre-chorus",
    "chorus":       "chorus",
    "bridge":       "bridge",
    "breakdown":    "breakdown",
    "outro":        "outro",
    "instrumental": "instrumental",
}

# Types that are candidates for deriving the 'drop' cue.
_DROP_CANDIDATES = {"chorus", "instrumental"}


def detect_cues(filepath: str, bpm: float) -> list[dict[str, Any]]:
    """Run allin1 + librosa cross-validation; return cue candidates.

    Each item: {'type': str, 'position': float, 'confidence': str, 'label': str}
    Positions are seconds from track start, snapped to the nearest allin1 downbeat.
    A derived 'drop' cue is added from the first chorus/instrumental segment.
    """
    if allin1 is None or librosa is None or np is None:
        raise RuntimeError(
            "Missing optional dependency 'embeddings'. Install with:\n\n"
            "    uv sync --extra embeddings\n"
        )

    # --- Primary: allin1 structural segmentation ---
    analysis = allin1.analyze(filepath)
    segments = analysis.segments or []
    downbeats = list(analysis.downbeats) if analysis.downbeats else []

    # --- Secondary: librosa energy + spectral transitions ---
    bar_duration = (60.0 / bpm * 4) if bpm > 0 else 2.0
    tolerance = bar_duration  # ±1 bar

    with _suppress_decoder_noise():
        y, sr = librosa.load(filepath, sr=22050, mono=True)

    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    chroma_diff = np.sum(np.abs(np.diff(chroma, axis=1)), axis=0)
    # Pad chroma_diff to match rms length
    pad = len(rms) - len(chroma_diff)
    if pad > 0:
        chroma_diff = np.pad(chroma_diff, (0, pad))
    else:
        chroma_diff = chroma_diff[: len(rms)]

    times = librosa.times_like(rms, sr=sr, hop_length=hop_length)

    def _norm(arr: Any) -> Any:
        m = float(arr.max())
        return arr / m if m > 0 else arr

    combined = _norm(rms) + _norm(flux) + _norm(chroma_diff)
    peak_indices = librosa.util.peak_pick(
        combined,
        pre_max=3, post_max=3, pre_avg=3, post_avg=5,
        delta=0.1, wait=10,
    )
    librosa_transitions = {float(times[i]) for i in peak_indices}

    # --- Snap helper: move position to nearest downbeat ---
    def _snap(pos: float) -> float:
        if not downbeats:
            return pos
        return min(downbeats, key=lambda d: abs(d - pos))

    # --- Build cues from allin1 segments ---
    cues: list[dict[str, Any]] = []
    drop_candidate: dict[str, Any] | None = None

    for seg in segments:
        label = (seg.label or "").lower()
        cue_type = _LABEL_MAP.get(label, "hot_cue")
        position = _snap(seg.start)

        agrees = any(abs(t - position) <= tolerance for t in librosa_transitions)
        confidence = "high" if agrees else "low"

        cues.append({
            "type": cue_type,
            "position": position,
            "confidence": confidence,
            "label": seg.label,
        })

        # Derive drop from first chorus/instrumental
        if drop_candidate is None and cue_type in _DROP_CANDIDATES:
            drop_candidate = {
                "type": "drop",
                "position": position,
                "confidence": confidence,
                "label": f"Drop ({seg.label})",
            }

    if drop_candidate is not None:
        cues.append(drop_candidate)

    return cues
```

- [ ] **Step 4: Run the detect_cues tests — confirm they pass**

```bash
.venv/bin/pytest tests/test_analyze_cues.py::test_detect_cues_returns_list_of_dicts \
                 tests/test_analyze_cues.py::test_detect_cues_high_confidence_when_librosa_agrees \
                 tests/test_analyze_cues.py::test_detect_cues_low_confidence_when_librosa_disagrees \
                 tests/test_analyze_cues.py::test_detect_cues_derives_drop_from_chorus \
                 tests/test_analyze_cues.py::test_detect_cues_snaps_to_nearest_downbeat \
                 -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add multidj/cues.py tests/test_analyze_cues.py
git commit -m "feat: add detect_cues() with allin1 + librosa cross-validation"
```

---

## Task 4: analyze_cues() — batch analysis command

**Files:**
- Modify: `multidj/cues.py` (append)
- Test: `tests/test_analyze_cues.py` (append)

- [ ] **Step 1: Add analyze_cues tests to tests/test_analyze_cues.py**

Append to `tests/test_analyze_cues.py`:

```python
# ---------------------------------------------------------------------------
# analyze_cues — integration tests (allin1 + librosa mocked)
# ---------------------------------------------------------------------------

_MOCK_DETECT_RETURN = [
    {"type": "intro",  "position": 0.0,   "confidence": "high", "label": "intro"},
    {"type": "verse",  "position": 30.0,  "confidence": "high", "label": "verse"},
    {"type": "chorus", "position": 90.0,  "confidence": "high", "label": "chorus"},
    {"type": "drop",   "position": 90.0,  "confidence": "high", "label": "Drop (chorus)"},
    {"type": "outro",  "position": 180.0, "confidence": "high", "label": "outro"},
]


def test_analyze_cues_dry_run_does_not_write(db):
    from multidj.cues import analyze_cues

    result = analyze_cues(db_path=str(db), apply=False)

    assert result["mode"] == "dry_run"
    assert result["total_candidates"] == 9
    assert result["processed"] == 0

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM cue_points").fetchone()[0]
    conn.close()
    assert count == 0


def test_analyze_cues_apply_stores_cue_points(db, tmp_path):
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        result = analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    assert result["mode"] == "apply"
    assert result["succeeded"] == 9
    assert result["failed"] == 0

    conn = sqlite3.connect(str(db))
    # Track 1 should have 5 cue points
    rows = conn.execute(
        "SELECT type, position, confidence, source FROM cue_points WHERE track_id=1 ORDER BY position"
    ).fetchall()
    conn.close()

    types = {r[0] for r in rows}
    assert "intro" in types
    assert "drop" in types
    assert "outro" in types
    assert all(r[2] == "high" for r in rows)
    assert all(r[3] == "auto" for r in rows)


def test_analyze_cues_apply_updates_tracks_shortcuts(db, tmp_path):
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT intro_end, outro_start FROM tracks WHERE id=1"
    ).fetchone()
    conn.close()

    # intro_end = position of first non-intro cue = 30.0 (verse)
    assert row[0] == pytest.approx(30.0)
    # outro_start = position of outro cue = 180.0
    assert row[1] == pytest.approx(180.0)


def test_analyze_cues_skips_tracks_with_existing_cues(db, tmp_path):
    from multidj.cues import analyze_cues

    # Pre-populate cues for track 1
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cue_points (track_id, type, position, confidence, source) VALUES (1, 'intro', 0.0, 'high', 'auto')"
    )
    conn.commit()
    conn.close()

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        result = analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    assert result["total_candidates"] == 8  # track 1 already has cues


def test_analyze_cues_force_reanalyzes_existing(db, tmp_path):
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    result = analyze_cues(db_path=str(db), apply=False)
    assert result["total_candidates"] == 0

    result2 = analyze_cues(db_path=str(db), apply=False, force=True)
    assert result2["total_candidates"] == 9


def test_analyze_cues_limit(db, tmp_path):
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        result = analyze_cues(db_path=str(db), apply=True, limit=3, backup_dir=str(tmp_path))

    assert result["processed"] == 3
    assert result["succeeded"] == 3


def test_analyze_cues_per_track_error_isolation(db, tmp_path):
    from multidj.cues import analyze_cues

    call_count = [0]

    def flaky(path, bpm):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("bad audio file")
        return _MOCK_DETECT_RETURN

    with patch("multidj.cues.detect_cues", side_effect=flaky):
        result = analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    assert result["failed"] == 1
    assert result["succeeded"] == 8
    assert len(result["errors"]) == 1


def test_clear_cues_dry_run(db, tmp_path):
    from multidj.cues import analyze_cues, clear_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    result = clear_cues(db_path=str(db), apply=False)
    assert result["mode"] == "dry_run"
    assert result["would_remove"] > 0

    # Dry-run: nothing actually removed
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM cue_points").fetchone()[0]
    conn.close()
    assert count > 0


def test_clear_cues_apply_removes_auto_cues(db, tmp_path):
    from multidj.cues import analyze_cues, clear_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    result = clear_cues(db_path=str(db), apply=True)
    assert result["mode"] == "apply"
    assert result["removed"] > 0

    conn = sqlite3.connect(str(db))
    count = conn.execute(
        "SELECT COUNT(*) FROM cue_points WHERE source='auto'"
    ).fetchone()[0]
    intro_end = conn.execute("SELECT intro_end FROM tracks WHERE id=1").fetchone()[0]
    outro_start = conn.execute("SELECT outro_start FROM tracks WHERE id=1").fetchone()[0]
    conn.close()

    assert count == 0
    assert intro_end is None
    assert outro_start is None


def test_clear_cues_preserves_manual_cues(db, tmp_path):
    from multidj.cues import analyze_cues, clear_cues

    # Insert a manual cue
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cue_points (track_id, type, position, confidence, source) "
        "VALUES (1, 'hot_cue', 42.0, 'high', 'manual')"
    )
    conn.commit()
    conn.close()

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    clear_cues(db_path=str(db), apply=True)

    conn = sqlite3.connect(str(db))
    manual_count = conn.execute(
        "SELECT COUNT(*) FROM cue_points WHERE source='manual'"
    ).fetchone()[0]
    conn.close()
    assert manual_count == 1
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
.venv/bin/pytest tests/test_analyze_cues.py -k "analyze_cues or clear_cues" -v 2>&1 | tail -10
```

Expected: failures because `analyze_cues` and `clear_cues` are not yet defined.

- [ ] **Step 3: Add analyze_cues() and clear_cues() to multidj/cues.py**

Append to `multidj/cues.py`:

```python
def analyze_cues(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: Any = False,
) -> dict[str, Any]:
    """Detect and store structural cues for tracks missing them.

    With force=True, re-analyzes tracks that already have cues.
    """
    from .db import connect, resolve_db_path, ensure_not_empty

    db_path = str(resolve_db_path(db_path))

    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)

        if force:
            where = "1=1"
        else:
            where = """
                NOT EXISTS (
                    SELECT 1 FROM cue_points cp
                    WHERE cp.track_id = t.id AND cp.source = 'auto'
                )
            """

        rows = conn.execute(
            f"SELECT id, path, bpm FROM tracks t WHERE deleted=0 AND {where} ORDER BY id"
        ).fetchall()

    candidates = [dict(r) for r in rows]
    total_candidates = len(candidates)
    if limit is not None:
        candidates = candidates[:limit]

    if not apply:
        _progress(
            f"Dry-run: {total_candidates:,} tracks would be analyzed "
            "(run with --apply to process)"
        )
        return {"mode": "dry_run", "total_candidates": total_candidates, "processed": 0}

    _progress(f"Analyzing {len(candidates):,} tracks for structural cues...")

    succeeded = 0
    failed = 0
    errors: list[dict[str, str]] = []

    with connect(db_path, readonly=False) as conn:
        for row in candidates:
            track_id = row["id"]
            filepath = row["path"]
            bpm = float(row["bpm"] or 120.0)
            _progress(f"  {filepath.split('/')[-1][:60]}", end=" ")
            try:
                cues = detect_cues(filepath, bpm)

                conn.execute(
                    "DELETE FROM cue_points WHERE track_id=? AND source='auto'",
                    (track_id,),
                )
                conn.executemany(
                    """
                    INSERT INTO cue_points (track_id, type, position, label, confidence, source)
                    VALUES (?, ?, ?, ?, ?, 'auto')
                    """,
                    [(track_id, c["type"], c["position"], c["label"], c["confidence"])
                     for c in cues],
                )

                # Denormalized shortcuts: intro_end = first non-intro position
                non_intro = [c for c in cues if c["type"] not in ("intro", "hot_cue")]
                intro_end = non_intro[0]["position"] if non_intro else None
                outro_cue = next((c for c in cues if c["type"] == "outro"), None)
                outro_start = outro_cue["position"] if outro_cue else None

                conn.execute(
                    "UPDATE tracks SET intro_end=?, outro_start=? WHERE id=?",
                    (intro_end, outro_start, track_id),
                )

                types = [c["type"] for c in cues if c["type"] not in ("hot_cue",)]
                marks = {c["type"]: "✓" if c["confidence"] == "high" else "" for c in cues}
                summary = " ".join(f"{t}{marks.get(t,'')}" for t in types[:6])
                _progress(f"→ {summary}")
                succeeded += 1
            except Exception as exc:
                failed += 1
                errors.append({"path": filepath, "reason": str(exc)})
                _progress(f"ERROR: {exc}")

        conn.commit()

    return {
        "mode": "apply",
        "total_candidates": total_candidates,
        "processed": len(candidates),
        "succeeded": succeeded,
        "failed": failed,
        "errors": errors,
    }


def clear_cues(
    db_path: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Remove all source='auto' cue points and reset tracks.intro_end / outro_start."""
    from .db import connect, resolve_db_path

    db_path = str(resolve_db_path(db_path))

    with connect(db_path, readonly=True) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM cue_points WHERE source='auto'"
        ).fetchone()[0]

    if not apply:
        _progress(f"Dry-run: {count:,} auto-detected cues would be removed")
        return {"mode": "dry_run", "would_remove": count}

    with connect(db_path, readonly=False) as conn:
        conn.execute("DELETE FROM cue_points WHERE source='auto'")
        conn.execute("UPDATE tracks SET intro_end=NULL, outro_start=NULL WHERE deleted=0")
        conn.commit()

    _progress(f"Removed {count:,} auto-detected cues")
    return {"mode": "apply", "removed": count}
```

- [ ] **Step 4: Run all analyze_cues and clear_cues tests**

```bash
.venv/bin/pytest tests/test_analyze_cues.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full test suite — confirm nothing is broken**

```bash
.venv/bin/pytest tests/ -v 2>&1 | tail -15
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add multidj/cues.py tests/test_analyze_cues.py
git commit -m "feat: add analyze_cues() and clear_cues() batch commands"
```

---

## Task 5: CLI wiring

**Files:**
- Modify: `multidj/cli.py`

- [ ] **Step 1: Add import at top of cli.py**

In `multidj/cli.py`, add to the imports section (after the existing imports):

```python
from .cues import analyze_cues as _analyze_cues, clear_cues as _clear_cues
```

- [ ] **Step 2: Add analyze cues subparser**

In `multidj/cli.py`, find the `analyze_sub` block (around line 187, after the `embed` parser). Add:

```python
    p_cues = analyze_sub.add_parser("cues", help="Detect structural cues (intro/verse/chorus/drop/outro)")
    p_cues.add_argument("--apply", action="store_true", help="Write cues to DB (default: dry-run)")
    p_cues.add_argument("--force", action="store_true", help="Re-analyze tracks that already have cues")
    p_cues.add_argument("--limit", type=int, default=None, help="Cap number of tracks to process")
```

- [ ] **Step 3: Add cues top-level command group**

In `multidj/cli.py`, after the `crates` block, add:

```python
    # ── cues ─────────────────────────────────────────────────────────────────
    cues_p = sub.add_parser("cues", help="Cue point management")
    cues_sub = cues_p.add_subparsers(dest="cues_target", required=True)

    p = cues_sub.add_parser("clear", help="Remove all auto-detected cues from DB")
    p.add_argument("--apply", action="store_true", help="Write removal (default: dry-run)")
```

- [ ] **Step 4: Wire the handlers in the main() function**

In the `main()` function of `multidj/cli.py`, find where `analyze_target` commands are dispatched. Add the `cues` case to the `analyze` dispatch block. Then add the `cues` command dispatch. 

Find the existing `elif args.command == "analyze":` block and add the `cues` handler:

```python
        elif args.analyze_target == "cues":
            result = _analyze_cues(
                db_path=db_path,
                apply=args.apply,
                force=args.force,
                limit=args.limit,
            )
            if json_mode:
                emit(result, json_mode)
```

Then find where other top-level commands are dispatched (after `elif args.command == "crates":`) and add:

```python
    elif args.command == "cues":
        if args.cues_target == "clear":
            result = _clear_cues(db_path=db_path, apply=args.apply)
            if json_mode:
                emit(result, json_mode)
```

- [ ] **Step 5: Smoke-test the CLI**

```bash
.venv/bin/python -m multidj analyze cues --help
.venv/bin/python -m multidj cues clear --help
```

Expected: both print help text with `--apply`, `--force`, `--limit` flags described.

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/pytest tests/ -v 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add multidj/cli.py
git commit -m "feat: wire analyze cues + cues clear CLI subcommands"
```

---

## Task 6: Pipeline integration

**Files:**
- Modify: `multidj/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Add pipeline cues tests to tests/test_pipeline.py**

Open `tests/test_pipeline.py` and find the existing step-presence tests. Add:

```python
def test_pipeline_includes_cues_step(multidj_db, tmp_path):
    """cues step appears in pipeline results."""
    from multidj.pipeline import run_pipeline
    from unittest.mock import patch

    with patch("multidj.cues.detect_cues", return_value=[]):
        result = run_pipeline(
            db_path=str(multidj_db),
            apply=False,
            skip=set(),
        )

    step_names = [s["step"] for s in result["steps"]]
    assert "cues" in step_names


def test_pipeline_skip_cues(multidj_db, tmp_path):
    """--skip-cues omits the step."""
    from multidj.pipeline import run_pipeline

    result = run_pipeline(
        db_path=str(multidj_db),
        apply=False,
        skip={"cues"},
    )

    cues_step = next(s for s in result["steps"] if s["step"] == "cues")
    assert cues_step["status"] == "skipped"


def test_pipeline_cues_after_energy(multidj_db):
    """cues step comes immediately after energy in the pipeline."""
    from multidj.pipeline import run_pipeline
    from unittest.mock import patch

    with patch("multidj.cues.detect_cues", return_value=[]):
        result = run_pipeline(
            db_path=str(multidj_db),
            apply=False,
            skip=set(),
        )

    step_names = [s["step"] for s in result["steps"]]
    energy_idx = step_names.index("energy")
    cues_idx = step_names.index("cues")
    assert cues_idx == energy_idx + 1
```

- [ ] **Step 2: Run the new pipeline tests — confirm they fail**

```bash
.venv/bin/pytest tests/test_pipeline.py::test_pipeline_includes_cues_step \
                 tests/test_pipeline.py::test_pipeline_skip_cues \
                 tests/test_pipeline.py::test_pipeline_cues_after_energy \
                 -v 2>&1 | tail -10
```

Expected: failures because the `cues` step isn't in the pipeline yet.

- [ ] **Step 3: Add cues step to multidj/pipeline.py**

In `multidj/pipeline.py`, add the import at the top:

```python
# (add to existing imports at top of file — no separate import block needed;
#  _run_cues uses a lazy import like _run_embed does)
```

Find the `# Step 7: Detect energy` block (around line 125). After the energy step and before the embed auto-skip block, add:

```python
    # Auto-skip cues if disabled in config
    if not cfg.get("pipeline", {}).get("cues", True):
        skip = skip | {"cues"}

    # Step 8: Detect structural cues (requires [embeddings] extra)
    def _run_cues(**kwargs):
        try:
            from .cues import analyze_cues as _ac
            return _ac(**kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "cues", _run_cues,
        db_path=db_path, apply=apply, backup_dir=False,
        limit=limit,
    ))
```

Also update the docstring on `run_pipeline` to reflect the new step order:

```python
    """Run the full MultiDJ pipeline: import → parse → dedupe → bpm → key → energy → cues → embed → cluster → genres → clean_text → crates → sync → report.
```

- [ ] **Step 4: Run the new pipeline tests — confirm they pass**

```bash
.venv/bin/pytest tests/test_pipeline.py -v 2>&1 | tail -15
```

Expected: all pipeline tests pass.

- [ ] **Step 5: Run full test suite**

```bash
.venv/bin/pytest tests/ -v 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add multidj/pipeline.py tests/test_pipeline.py
git commit -m "feat: insert cues as pipeline step 8 (after energy, before embed)"
```

---

## Task 7: Mixxx sync — add cues table to fixture + push cues

**Files:**
- Modify: `tests/fixtures/mixxx_factory.py`
- Modify: `multidj/adapters/mixxx.py`
- Create: `tests/test_mixxx_cue_sync.py`

- [ ] **Step 1: Add cues table to Mixxx fixture DDL**

In `tests/fixtures/mixxx_factory.py`, find `_DDL` and append the cues table definition:

```python
_DDL = """
... (existing tables) ...

CREATE TABLE IF NOT EXISTS cues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id   INTEGER NOT NULL,
    type       INTEGER NOT NULL DEFAULT 1,
    position   REAL NOT NULL DEFAULT -1,
    length     REAL NOT NULL DEFAULT 0,
    hotcue     INTEGER NOT NULL DEFAULT -1,
    label      TEXT NOT NULL DEFAULT '',
    color      INTEGER NOT NULL DEFAULT 4294967295
);
"""
```

- [ ] **Step 2: Write tests/test_mixxx_cue_sync.py**

```python
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.fixtures.mixxx_factory import make_mixxx_db
from tests.fixtures.multidj_factory import make_multidj_db


@pytest.fixture()
def multidj_db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


@pytest.fixture()
def mixxx_db(tmp_path):
    return make_mixxx_db(tmp_path / "mixxxdb.sqlite")


_MOCK_DETECT_RETURN = [
    {"type": "intro",  "position": 0.0,   "confidence": "high", "label": "intro"},
    {"type": "chorus", "position": 90.0,  "confidence": "high", "label": "chorus"},
    {"type": "drop",   "position": 90.0,  "confidence": "high", "label": "Drop (chorus)"},
    {"type": "outro",  "position": 180.0, "confidence": "high", "label": "outro"},
]


def _populate_cues(multidj_db, tmp_path):
    """Helper: run analyze_cues with mocked detect_cues to populate cue_points."""
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(multidj_db), apply=True, backup_dir=str(tmp_path))


def test_sync_pushes_intro_drop_outro_to_mixxx(multidj_db, mixxx_db, tmp_path):
    _populate_cues(multidj_db, tmp_path)

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=str(mixxx_db))
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    cue_rows = conn.execute(
        "SELECT label FROM cues ORDER BY label"
    ).fetchall()
    conn.close()

    labels = {r[0] for r in cue_rows}
    assert "Drop" in labels
    assert "Intro" in labels
    assert "Outro" in labels


def test_sync_does_not_push_low_confidence_cues(multidj_db, mixxx_db, tmp_path):
    low_conf = [
        {"type": "intro",  "position": 0.0,   "confidence": "low",  "label": "intro"},
        {"type": "drop",   "position": 90.0,  "confidence": "low",  "label": "Drop (chorus)"},
        {"type": "outro",  "position": 180.0, "confidence": "low",  "label": "outro"},
    ]
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=low_conf):
        analyze_cues(db_path=str(multidj_db), apply=True, backup_dir=str(tmp_path))

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=str(mixxx_db))
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    cue_count = conn.execute("SELECT COUNT(*) FROM cues").fetchone()[0]
    conn.close()

    assert cue_count == 0


def test_sync_cue_positions_in_sample_frames(multidj_db, mixxx_db, tmp_path):
    _populate_cues(multidj_db, tmp_path)

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=str(mixxx_db))
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    drop_row = conn.execute(
        "SELECT position FROM cues WHERE label='Drop'"
    ).fetchone()
    conn.close()

    # Drop is at 90.0s; position should be 90.0 * 44100 = 3_969_000 sample frames
    assert drop_row is not None
    assert drop_row[0] == pytest.approx(90.0 * 44100, rel=0.01)


def test_sync_reconciles_stale_cues(multidj_db, mixxx_db, tmp_path):
    """After clear_cues, old Mixxx cues are removed on next sync."""
    _populate_cues(multidj_db, tmp_path)

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=str(mixxx_db))
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    # Clear all auto cues from MultiDJ
    from multidj.cues import clear_cues
    clear_cues(db_path=str(multidj_db), apply=True)

    # Sync again — Mixxx cues should be removed
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM cues WHERE label IN ('Intro','Drop','Outro')"
    ).fetchone()[0]
    conn.close()

    assert remaining == 0
```

- [ ] **Step 3: Run the new Mixxx tests — confirm they fail**

```bash
.venv/bin/pytest tests/test_mixxx_cue_sync.py -v 2>&1 | tail -15
```

Expected: failures because `_push_cues_to_mixxx` doesn't exist yet.

- [ ] **Step 4: Add _push_cues_to_mixxx() to multidj/adapters/mixxx.py**

At the bottom of the module-level functions in `multidj/adapters/mixxx.py` (before the `MixxxAdapter` class), add:

```python
_SAMPLE_RATE = 44100

# Mixxx ARGB colors for each cue type
_CUE_COLORS = {
    "intro": 0xFF0000FF,  # Blue
    "drop":  0xFFFF0000,  # Red
    "outro": 0xFF00FF00,  # Green
}

# Hot cue slot assignments
_CUE_HOTCUE_SLOTS = {"intro": 0, "drop": 1, "outro": 2}


def _push_cues_to_mixxx(
    mdj_conn: sqlite3.Connection,
    mixxx_conn: sqlite3.Connection,
) -> dict:
    """Write high-confidence intro/drop/outro cues from MultiDJ to Mixxx cues table."""
    cue_rows = mdj_conn.execute(
        """
        SELECT t.path, cp.type, cp.position, cp.label
        FROM cue_points cp
        JOIN tracks t ON cp.track_id = t.id
        WHERE cp.type IN ('intro', 'drop', 'outro')
          AND cp.confidence = 'high'
          AND cp.source = 'auto'
          AND t.deleted = 0
        """
    ).fetchall()

    pushed = 0
    for path, cue_type, position_secs, _ in cue_rows:
        row = mixxx_conn.execute(
            """
            SELECT l.id FROM library l
            JOIN track_locations tl ON l.location = tl.id
            WHERE tl.location = ? AND l.mixxx_deleted = 0
            """,
            (path,),
        ).fetchone()
        if not row:
            continue

        mixxx_track_id = row[0]
        position_frames = float(position_secs) * _SAMPLE_RATE
        hotcue_slot = _CUE_HOTCUE_SLOTS[cue_type]
        color = _CUE_COLORS.get(cue_type, 0xFFFFFFFF)
        display_label = cue_type.capitalize()

        # Reconcile: delete existing cue in this hotcue slot for this track
        mixxx_conn.execute(
            "DELETE FROM cues WHERE track_id=? AND hotcue=?",
            (mixxx_track_id, hotcue_slot),
        )
        mixxx_conn.execute(
            """
            INSERT INTO cues (track_id, type, position, length, hotcue, label, color)
            VALUES (?, 1, ?, 0, ?, ?, ?)
            """,
            (mixxx_track_id, position_frames, hotcue_slot, display_label, color),
        )
        pushed += 1

    return {"pushed": pushed}
```

- [ ] **Step 5: Call _push_cues_to_mixxx from full_sync**

In `multidj/adapters/mixxx.py`, find the `full_sync` method. After the `_push_crates_to_mixxx` call (near the end of the apply block), add:

```python
                cue_result = _push_cues_to_mixxx(mdj_conn, mixxx_conn)
```

In `full_sync`, the apply block ends with `return { "total_dirty": ..., "succeeded": ..., ... }`. Find the inner apply block that calls `_push_crates_to_mixxx`. After that call, add the cue sync lines and include `cues_pushed` in the return dict. The full apply-block tail should look like:

```python
                crate_result = _push_crates_to_mixxx(mdj_conn, mixxx_conn)

                # Reconcile cues: wipe slots 0/1/2 then repopulate from MultiDJ.
                # MultiDJ is source of truth — same pattern as crates.
                mixxx_conn.execute("DELETE FROM cues WHERE hotcue IN (0, 1, 2)")
                cue_result = _push_cues_to_mixxx(mdj_conn, mixxx_conn)

            return {
                "mode": "apply",
                "total_dirty":        len(dirty_tracks),
                "succeeded":          len(dirty_tracks) - len(errors),
                "errors":             len(errors),
                "error_details":      errors,
                "crates_synced":      crate_result,
                "cues_pushed":        cue_result.get("pushed", 0),
            }
```

(Match the existing return dict keys already present in `full_sync` — add `cues_pushed` without removing any existing key.)

- [ ] **Step 6: Run the Mixxx cue sync tests**

```bash
.venv/bin/pytest tests/test_mixxx_cue_sync.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 7: Run the full test suite**

```bash
.venv/bin/pytest tests/ -v 2>&1 | tail -15
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add tests/fixtures/mixxx_factory.py multidj/adapters/mixxx.py tests/test_mixxx_cue_sync.py
git commit -m "feat: sync intro/drop/outro hot cues to Mixxx on full_sync"
```

---

## Task 8: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.agent-handoff/layer1a.md` (or whichever layer covers commands + architecture)

- [ ] **Step 1: Update CLAUDE.md commands table**

In `CLAUDE.md`, in the Commands table, add two rows after `analyze energy`:

```markdown
| `analyze cues` | Structural segmentation (intro/verse/chorus/drop/outro) via allin1 + librosa; `--apply`, `--force`, `--limit` (requires embeddings extra) |
| `cues clear` | Remove all auto-detected cues from DB; `--apply` |
```

- [ ] **Step 2: Update CLAUDE.md pipeline description**

In `CLAUDE.md`, update the `pipeline` row description to read:

```
Primary daily workflow: chains all 15 steps; `--apply`, `--skip-<step>`, `--music-dir`
```

And in the Architecture section, update the pipeline docstring reference to:

```
import → fix_mismatches → parse → dedupe → bpm → key → energy → cues → embed → cluster → genres → clean_text → crates → sync → report
```

- [ ] **Step 3: Update CLAUDE.md embeddings extra description**

In the Installation section, update the embeddings line:

```markdown
uv sync --extra embeddings        # + torch, torchaudio, transformers, librosa, umap, hdbscan, openai, allin1
```

- [ ] **Step 4: Update agent handoff docs**

Open `.agent-handoff/layer1a.md` (or whichever layer file covers the module list and schema). Add:

```markdown
**`cues.py`** — `detect_cues(filepath, bpm)` runs allin1 (primary) + librosa (secondary) to return cross-validated cue candidates; `analyze_cues()` and `clear_cues()` are the batch DB commands. All auto-generated cues have `source='auto'`; manual cues have `source='manual'` and are never overwritten.

**`cue_points` schema** (migration 005): added `confidence TEXT DEFAULT 'high'` ('high' = both allin1 + librosa agree, 'low' = allin1 only) and `source TEXT DEFAULT 'auto'`. Only high-confidence auto cues are pushed to Mixxx.

**Mixxx cue sync**: `_push_cues_to_mixxx()` in `adapters/mixxx.py` writes intro (slot 0, blue), drop (slot 1, red), outro (slot 2, green) as hot cues. Called from `full_sync()` — slots 0/1/2 are wiped and repopulated on every sync (same reconcile pattern as crates).
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .agent-handoff/
git commit -m "docs: update CLAUDE.md and agent handoff for Phase 13 cue detection"
```

---

## Task 9: Final verification

- [ ] **Step 1: Run the complete test suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests pass. Count should be higher than before (132 + new tests).

- [ ] **Step 2: Smoke-test the CLI end-to-end (dry-run only — no real audio needed)**

```bash
.venv/bin/python -m multidj analyze cues --help
.venv/bin/python -m multidj cues clear --help
.venv/bin/python -m multidj pipeline --help | grep skip
```

Expected: all print help without errors. `--skip-cues` appears in pipeline help.

- [ ] **Step 3: Final commit and push**

```bash
git status
git log --oneline feat/cue-detection ^dev
```

Review the commit list — should be 8 commits (Tasks 0–8). Then open a PR:

```bash
gh pr create \
  --base dev \
  --title "feat: Phase 13 — automatic cue detection (allin1 + librosa)" \
  --body "Adds structural segmentation pipeline step. Intro/drop/outro synced to Mixxx as hot cues. See spec: docs/superpowers/specs/2026-05-28-cue-detection-design.md"
```
