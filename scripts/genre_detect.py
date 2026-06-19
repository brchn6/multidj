#!/usr/bin/env python3
"""Zero-shot genre detection for tracks missing genre metadata.

Strategy (applied in order, stops when confident):
  1. CLAP zero-shot: compute cosine similarity between the track's stored
     CLAP audio embedding and a set of genre text prompts. Fast because no
     audio I/O — uses vectors already in the DB.
  2. Folder-name heuristic: map the track's parent folder path to a known
     genre using a curated keyword table. Instant, no model needed.

Only writes genre when no existing genre is set AND confidence >= threshold.

Usage:
    python scripts/genre_detect.py [--db PATH] [--apply] [--limit N] [--min-conf 0.20]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Genre prompts for CLAP zero-shot classification
# ---------------------------------------------------------------------------
GENRE_PROMPTS: dict[str, list[str]] = {
    "House": [
        "house music with four-on-the-floor kick drum",
        "classic house music Chicago style",
        "deep house groove",
    ],
    "Tech House": [
        "tech house music minimal and driving",
        "tech house with hypnotic bass loop",
    ],
    "Deep House": [
        "deep house music soulful and smooth",
        "deep house with warm chords and bass",
    ],
    "Progressive House": [
        "progressive house music with builds and drops",
        "melodic progressive house trance-influenced",
    ],
    "Techno": [
        "techno music with dark industrial kick",
        "Berlin style techno minimal and hypnotic",
        "hard techno rave",
    ],
    "Trance": [
        "trance music euphoric and uplifting",
        "psytrance with rapid arpeggios",
        "progressive trance build and breakdown",
    ],
    "Drum & Bass": [
        "drum and bass music with fast breakbeats",
        "liquid drum and bass smooth and melodic",
        "neurofunk drum and bass heavy bass",
    ],
    "Dubstep": [
        "dubstep music with heavy wobble bass drop",
        "future garage slow dubstep",
    ],
    "UK Garage": [
        "UK garage music shuffled beats two-step",
        "UK garage with vocals and syncopated drums",
    ],
    "Disco": [
        "disco music funky bass and strings",
        "classic disco four-on-the-floor with horns",
    ],
    "Funk": [
        "funk music with slap bass and wah guitar",
        "funky groove with brass section",
    ],
    "Hip Hop": [
        "hip hop beat with sampled breakbeat",
        "rap music with booming 808 bass",
    ],
    "Afro House": [
        "afro house music with African percussion",
        "afrobeats house music warm bass",
    ],
    "Dancehall": [
        "dancehall reggae music with ragga MC",
        "digital dancehall with skank rhythm",
    ],
    "Jungle": [
        "jungle music with Amen break and heavy bass",
        "ragga jungle fast breakbeats",
    ],
    "Breaks": [
        "breakbeat music with sampled hip hop drums",
        "big beat breaks with rock guitars",
    ],
    "Electro": [
        "electro music with Roland 808 drum machine",
        "electro Miami bass boogie",
    ],
    "Hardstyle": [
        "hardstyle music with distorted kick and melody",
        "hard dance euphoric hardstyle",
    ],
    "Electronic": [
        "electronic music experimental synthesizer",
        "ambient electronic music atmospheric",
    ],
    "Pop": [
        "pop music catchy melody and vocals",
        "dance pop upbeat with synthesizers",
    ],
    "R&B": [
        "R&B soul music with smooth vocals",
        "contemporary R&B with electronic production",
    ],
    "Reggaeton": [
        "reggaeton music with dembow rhythm",
        "latin urban music with dancehall influence",
    ],
    "Israeli": [
        "Israeli pop music with Hebrew lyrics",
        "mizrahi music Middle Eastern scale Hebrew",
    ],
}

# ---------------------------------------------------------------------------
# Folder → genre heuristic table
# ---------------------------------------------------------------------------
_FOLDER_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"disco", re.I),           "Disco"),
    (re.compile(r"ukg|uk.?garage|speed.?garage", re.I), "UK Garage"),
    (re.compile(r"jungle|dnb|drum.?n.?bass", re.I), "Drum & Bass"),
    (re.compile(r"techno|rave", re.I),     "Techno"),
    (re.compile(r"house", re.I),           "House"),
    (re.compile(r"trance", re.I),          "Trance"),
    (re.compile(r"hip.?hop|rap", re.I),    "Hip Hop"),
    (re.compile(r"reggaeton", re.I),       "Reggaeton"),
    (re.compile(r"r.?b|soul", re.I),       "R&B"),
    (re.compile(r"funk", re.I),            "Funk"),
    (re.compile(r"afro", re.I),            "Afro House"),
    (re.compile(r"dancehall", re.I),       "Dancehall"),
    (re.compile(r"breaks?", re.I),         "Breaks"),
    (re.compile(r"hebrew|israel|עברית|ישראל", re.I), "Israeli"),
    (re.compile(r"hardstyle|hard.?dance", re.I), "Hardstyle"),
    (re.compile(r"electro", re.I),         "Electro"),
    (re.compile(r"pop|mainstream", re.I),  "Pop"),
    (re.compile(r"groove|clasic_grove", re.I), "Funk"),
    (re.compile(r"brasil|latin|salsa|samba", re.I), "Latin"),
    (re.compile(r"moombah", re.I),         "Moombahton"),
    (re.compile(r"dubstep", re.I),         "Dubstep"),
]

MUSIC_ROOT = "/home/barc/Weizmann Institute Dropbox/Bar Cohen/Music"


def _folder_genre(path: str) -> str | None:
    try:
        rel = Path(path).relative_to(MUSIC_ROOT)
    except ValueError:
        rel = Path(path)
    folder = str(rel.parts[0]) if rel.parts else ""
    for pattern, genre in _FOLDER_RULES:
        if pattern.search(folder):
            return genre
    return None


# ---------------------------------------------------------------------------
# CLAP zero-shot (uses stored embeddings — no audio I/O)
# ---------------------------------------------------------------------------

def _build_genre_text_embeddings(model, processor, device: str) -> dict[str, "np.ndarray"]:
    """Encode all genre text prompts and return mean embedding per genre."""
    import torch
    import numpy as np

    genre_vecs: dict[str, list] = {}
    for genre, prompts in GENRE_PROMPTS.items():
        vecs = []
        for prompt in prompts:
            inputs = processor(text=[prompt], return_tensors="pt", padding=True)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            with torch.no_grad():
                feat = model.get_text_features(**inputs)
            # get_text_features returns BaseModelOutputWithPooling; use pooler_output
            vec = feat.pooler_output[0].cpu().numpy()
            vecs.append(vec)
        genre_vecs[genre] = np.mean(vecs, axis=0)
    return genre_vecs


def _cosine_sim(a: "np.ndarray", b: "np.ndarray") -> float:
    import numpy as np
    na = np.linalg.norm(a) + 1e-8
    nb = np.linalg.norm(b) + 1e-8
    return float(np.dot(a / na, b / nb))


def clap_zero_shot_genre(
    audio_vec: "np.ndarray",
    genre_text_vecs: dict[str, "np.ndarray"],
    min_conf: float = 0.18,
) -> tuple[str | None, float]:
    """Return (best_genre, confidence) or (None, 0) if below threshold."""
    import numpy as np

    scores = {g: _cosine_sim(audio_vec, tv) for g, tv in genre_text_vecs.items()}
    # Softmax to get probability-like scores
    vals = np.array(list(scores.values()))
    vals_exp = np.exp((vals - vals.max()) * 10)
    probs = vals_exp / vals_exp.sum()
    prob_dict = dict(zip(scores.keys(), probs.tolist()))
    best = max(prob_dict, key=prob_dict.get)
    return (best, prob_dict[best]) if prob_dict[best] >= min_conf else (None, 0.0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",      default=None)
    parser.add_argument("--apply",   action="store_true")
    parser.add_argument("--limit",   type=int, default=None)
    parser.add_argument("--min-conf", type=float, default=0.20, dest="min_conf")
    args = parser.parse_args()

    # Resolve DB
    if args.db:
        db_path = args.db
    else:
        try:
            import tomllib  # type: ignore
        except ImportError:
            import tomli as tomllib  # type: ignore
        cfg_path = Path.home() / ".multidj" / "config.toml"
        with open(cfg_path, "rb") as f:
            cfg = tomllib.load(f)
        db_path = cfg.get("db", {}).get("path") or str(Path.home() / ".multidj" / "library.sqlite")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Fetch tracks with no genre
    rows = conn.execute("""
        SELECT t.id, t.path, e.vector
        FROM tracks t
        LEFT JOIN embeddings e ON t.id = e.track_id
        WHERE t.deleted = 0 AND (t.genre IS NULL OR t.genre = '')
        ORDER BY t.id
    """).fetchall()

    if args.limit:
        rows = rows[:args.limit]

    total = len(rows)
    print(f"Tracks without genre: {total}", file=sys.stderr)

    # Split by what data we have
    with_embed = [(r["id"], r["path"], r["vector"]) for r in rows if r["vector"]]
    without_embed = [(r["id"], r["path"]) for r in rows if not r["vector"]]

    print(f"  With CLAP embeddings: {len(with_embed)}", file=sys.stderr)
    print(f"  Folder heuristic only: {len(without_embed)}", file=sys.stderr)

    results: list[dict] = []

    # --- CLAP zero-shot for tracks with embeddings ---
    if with_embed:
        import numpy as np
        try:
            import torch
            from transformers import ClapModel, ClapProcessor  # type: ignore
            device = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"Loading CLAP model for zero-shot genre classification on {device}…", file=sys.stderr)
            clap_model = ClapModel.from_pretrained("laion/larger_clap_music").to(device)
            clap_proc = ClapProcessor.from_pretrained("laion/larger_clap_music")
            clap_model.eval()
            genre_vecs = _build_genre_text_embeddings(clap_model, clap_proc, device)

            for tid, path, blob in with_embed:
                audio_vec = np.frombuffer(blob, dtype=np.float32).copy()
                genre, conf = clap_zero_shot_genre(audio_vec, genre_vecs, args.min_conf)
                if genre:
                    results.append({"id": tid, "path": path, "genre": genre, "conf": conf, "method": "clap"})
                else:
                    # Fallback to folder
                    g = _folder_genre(path)
                    if g:
                        results.append({"id": tid, "path": path, "genre": g, "conf": 0.5, "method": "folder"})

        except ImportError:
            print("CLAP not available, falling back to folder heuristic for embedded tracks", file=sys.stderr)
            for tid, path, _ in with_embed:
                g = _folder_genre(path)
                if g:
                    results.append({"id": tid, "path": path, "genre": g, "conf": 0.5, "method": "folder"})

    # --- Folder heuristic for tracks without embeddings ---
    for tid, path in without_embed:
        g = _folder_genre(path)
        if g:
            results.append({"id": tid, "path": path, "genre": g, "conf": 0.5, "method": "folder"})

    # Report
    from collections import Counter
    method_counts = Counter(r["method"] for r in results)
    genre_counts = Counter(r["genre"] for r in results)

    print(f"\nResults: {len(results)}/{total} tracks assigned a genre", file=sys.stderr)
    print(f"  By method: {dict(method_counts)}", file=sys.stderr)
    print(f"  Top genres: {dict(genre_counts.most_common(10))}", file=sys.stderr)

    if not args.apply:
        print("\nDry run — pass --apply to write genres to DB", file=sys.stderr)
        # Print sample
        for r in results[:20]:
            p = Path(r["path"]).name[:60]
            print(f"  [{r['method']:6s} {r['conf']:.2f}] {r['genre']:20s}  {p}", file=sys.stderr)
        return

    # Write
    updated = 0
    for r in results:
        conn.execute(
            "UPDATE tracks SET genre = ? WHERE id = ? AND (genre IS NULL OR genre = '')",
            (r["genre"], r["id"]),
        )
        updated += conn.total_changes - updated if updated == 0 else 0
    conn.commit()
    conn.close()
    print(f"\nWrote genre to {len(results)} tracks", file=sys.stderr)


if __name__ == "__main__":
    main()
