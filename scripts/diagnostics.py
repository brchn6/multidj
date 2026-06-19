#!/usr/bin/env python3
"""MultiDJ embedding & library diagnostics dashboard.

Produces a self-contained HTML page with six panels:

  1. Library Coverage      — % of tracks with BPM / Key / Genre / Energy / Embedding
  2. Genre Distribution    — Top-20 genres by track count
  3. BPM Distribution      — Histogram across the whole library
  4. Key Distribution      — Bar chart of Camelot key usage
  5. Embedding Similarity  — Pairwise cosine-sim histogram (200-track sample)
  6. Cluster Diagnostics   — Intra-cluster vs inter-cluster similarity; cluster sizes

Usage:
    python scripts/diagnostics.py [--db PATH] [--out PATH] [--sample N]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _load_library(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT t.id, t.artist, t.title, t.genre, t.bpm, t.key, t.energy
        FROM tracks t
        WHERE t.deleted = 0
    """).fetchall()
    return [dict(r) for r in rows]


def _load_embeddings(conn: sqlite3.Connection, model: str, sample: int | None) -> tuple[list[int], np.ndarray]:
    rows = conn.execute("""
        SELECT e.track_id, e.vector
        FROM embeddings e
        JOIN tracks t ON e.track_id = t.id
        WHERE t.deleted = 0 AND e.model_name = ?
        ORDER BY RANDOM()
    """, (model,)).fetchall()
    if sample and len(rows) > sample:
        rows = rows[:sample]
    if not rows:
        return [], np.empty((0, 0), dtype=np.float32)
    ids = [r["track_id"] for r in rows]
    vecs = [np.frombuffer(r["vector"], dtype=np.float32).copy() for r in rows]
    return ids, np.stack(vecs)


# ---------------------------------------------------------------------------
# Panel 1: Coverage
# ---------------------------------------------------------------------------

def _coverage_data(tracks: list[dict], n_embedded: int) -> dict:
    n = len(tracks)
    fields = {
        "BPM": sum(1 for t in tracks if t.get("bpm")),
        "Key": sum(1 for t in tracks if t.get("key")),
        "Genre": sum(1 for t in tracks if t.get("genre")),
        "Energy": sum(1 for t in tracks if t.get("energy") is not None),
        "Embedding (CLAP)": n_embedded,
    }
    return {
        "fields": list(fields.keys()),
        "counts": list(fields.values()),
        "pcts": [round(v / n * 100, 1) for v in fields.values()],
        "total": n,
    }


# ---------------------------------------------------------------------------
# Panel 2: Genre distribution
# ---------------------------------------------------------------------------

def _genre_data(tracks: list[dict], top_n: int = 20) -> dict:
    from collections import Counter
    counts = Counter(t["genre"] for t in tracks if t.get("genre"))
    top = counts.most_common(top_n)
    return {
        "genres": [g for g, _ in top],
        "counts": [c for _, c in top],
    }


# ---------------------------------------------------------------------------
# Panel 3: BPM distribution
# ---------------------------------------------------------------------------

def _bpm_data(tracks: list[dict]) -> dict:
    bpms = [float(t["bpm"]) for t in tracks if t.get("bpm") and 50 < float(t["bpm"]) < 250]
    return {"bpms": bpms}


# ---------------------------------------------------------------------------
# Panel 4: Key distribution (Camelot)
# ---------------------------------------------------------------------------

_CAMELOT_ORDER = [
    "1A", "1B", "2A", "2B", "3A", "3B", "4A", "4B", "5A", "5B", "6A", "6B",
    "7A", "7B", "8A", "8B", "9A", "9B", "10A", "10B", "11A", "11B", "12A", "12B",
]

from multidj.constants import CAMELOT_KEY_MAP as _CAMELOT_MAP
import re as _re
_CAM_RE = _re.compile(r"^(\d{1,2})([AB])$", _re.IGNORECASE)


def _to_camelot(key: str | None) -> str | None:
    if not key:
        return None
    k = key.strip()
    if _CAM_RE.match(k):
        return k.upper()
    return _CAMELOT_MAP.get(k)


def _key_data(tracks: list[dict]) -> dict:
    from collections import Counter
    counts: Counter = Counter()
    for t in tracks:
        c = _to_camelot(t.get("key"))
        if c:
            counts[c] += 1
    return {
        "keys": _CAMELOT_ORDER,
        "counts": [counts.get(k, 0) for k in _CAMELOT_ORDER],
    }


# ---------------------------------------------------------------------------
# Panel 5: Embedding similarity distribution
# ---------------------------------------------------------------------------

def _sim_distribution(vectors: np.ndarray) -> dict:
    if len(vectors) < 2:
        return {"sims": [], "mean": 0, "min": 0, "max": 0, "n": 0}
    arr = vectors.copy()
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
    arr = arr / norms
    sims = arr @ arr.T  # (n, n)
    # Upper triangle only (exclude self-sim)
    idx = np.triu_indices(len(arr), k=1)
    flat = sims[idx]
    return {
        "sims": flat.tolist(),
        "mean": round(float(np.mean(flat)), 4),
        "std": round(float(np.std(flat)), 4),
        "min": round(float(np.min(flat)), 4),
        "max": round(float(np.max(flat)), 4),
        "n": len(flat),
    }


# ---------------------------------------------------------------------------
# Panel 6: Cluster diagnostics
# ---------------------------------------------------------------------------

def _cluster_diagnostics(track_ids: list[int], vectors: np.ndarray,
                          conn: sqlite3.Connection,
                          tracks: list[dict]) -> dict:
    """Run UMAP→HDBSCAN and compute per-cluster stats."""
    if len(vectors) < 30:
        return {"available": False, "reason": f"Only {len(vectors)} embeddings (need ≥30)"}

    try:
        import umap  # type: ignore
        import hdbscan  # type: ignore
    except ImportError:
        return {"available": False, "reason": "umap/hdbscan not installed"}

    n = len(vectors)
    n_components = min(10, n - 2)
    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=min(15, n - 1),
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    print("  Running UMAP for cluster diagnostics…", file=sys.stderr)
    reduced = reducer.fit_transform(vectors)

    mcs = max(10, n // 80)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=mcs, min_samples=3, metric="euclidean")
    labels = clusterer.fit_predict(reduced)

    # Build track_id → track metadata lookup
    tid_to_track = {t["id"]: t for t in tracks}

    # Per-cluster stats
    unique_labels = sorted(set(labels))
    cluster_stats = []
    for lbl in unique_labels:
        mask = labels == lbl
        idx_in_cluster = np.where(mask)[0]
        c_vecs = vectors[mask]
        c_tids = [track_ids[i] for i in idx_in_cluster]

        # Normalise and compute intra-cluster mean cosine sim
        c_norms = np.linalg.norm(c_vecs, axis=1, keepdims=True) + 1e-8
        c_norm = c_vecs / c_norms
        if len(c_norm) > 1:
            c_sims = c_norm @ c_norm.T
            tri_idx = np.triu_indices(len(c_norm), k=1)
            intra_sim = float(np.mean(c_sims[tri_idx]))
        else:
            intra_sim = 1.0

        # Genre distribution in cluster
        from collections import Counter
        genre_counts = Counter(
            tid_to_track.get(tid, {}).get("genre") or "(none)"
            for tid in c_tids
        )
        dominant_genre, dominant_count = genre_counts.most_common(1)[0]
        genre_purity = round(dominant_count / len(c_tids) * 100, 1)

        # BPM stats
        bpms = [
            float(tid_to_track.get(tid, {}).get("bpm") or 0)
            for tid in c_tids
            if tid_to_track.get(tid, {}).get("bpm")
        ]
        bpm_mean = round(float(np.mean(bpms)), 1) if bpms else None
        bpm_std = round(float(np.std(bpms)), 1) if bpms else None

        cluster_stats.append({
            "label": int(lbl),
            "name": "Noise" if lbl == -1 else f"Cluster {lbl}",
            "size": int(mask.sum()),
            "intra_sim": round(intra_sim, 4),
            "dominant_genre": dominant_genre,
            "genre_purity": genre_purity,
            "bpm_mean": bpm_mean,
            "bpm_std": bpm_std,
        })

    # Overall inter-cluster similarity: sample cross-cluster pairs
    rng = np.random.default_rng(42)
    inter_sims = []
    cl_labels = [l for l in unique_labels if l >= 0]
    if len(cl_labels) >= 2:
        for _ in range(min(5000, 200 * len(cl_labels))):
            la, lb = rng.choice(cl_labels, size=2, replace=False)
            ia = rng.choice(np.where(labels == la)[0])
            ib = rng.choice(np.where(labels == lb)[0])
            va = vectors[ia] / (np.linalg.norm(vectors[ia]) + 1e-8)
            vb = vectors[ib] / (np.linalg.norm(vectors[ib]) + 1e-8)
            inter_sims.append(float(va @ vb))
    inter_mean = round(float(np.mean(inter_sims)), 4) if inter_sims else None

    return {
        "available": True,
        "n_clusters": len(cl_labels),
        "n_noise": int((labels == -1).sum()),
        "inter_cluster_mean_sim": inter_mean,
        "clusters": cluster_stats,
    }


# ---------------------------------------------------------------------------
# HTML render
# ---------------------------------------------------------------------------

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MultiDJ Diagnostics</title>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
  <style>
    body {{ margin: 0; background: #0e1117; color: #ddd; font-family: monospace; }}
    h1 {{ text-align: center; color: #a0c4ff; padding: 18px 0 4px; font-size: 1.2rem; margin: 0; }}
    .subtitle {{ text-align: center; color: #666; font-size: 0.8rem; margin-bottom: 14px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; padding: 14px; }}
    .panel {{ background: #161b27; border: 1px solid #2a3550; border-radius: 8px; padding: 12px; }}
    .panel h2 {{ margin: 0 0 8px; font-size: 0.9rem; color: #88c0d0; }}
    .stats {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }}
    .stat-box {{ background: #1e2942; border-radius: 6px; padding: 6px 12px; text-align: center; }}
    .stat-val {{ font-size: 1.4rem; color: #a0c4ff; font-weight: bold; }}
    .stat-lbl {{ font-size: 0.68rem; color: #888; }}
    .cluster-table {{ width: 100%; border-collapse: collapse; font-size: 0.72rem; margin-top: 8px; }}
    .cluster-table th {{ color: #88c0d0; text-align: left; border-bottom: 1px solid #333; padding: 3px 6px; }}
    .cluster-table td {{ padding: 3px 6px; border-bottom: 1px solid #1e2535; }}
    .cluster-table tr:hover td {{ background: #1e2942; }}
    .noise-row td {{ color: #666; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<h1>MultiDJ Library Diagnostics</h1>
<div class="subtitle">{subtitle}</div>
<div class="stats" style="padding: 0 14px 10px; justify-content: center;">
  <div class="stat-box"><div class="stat-val">{total}</div><div class="stat-lbl">Active Tracks</div></div>
  <div class="stat-box"><div class="stat-val">{pct_bpm}%</div><div class="stat-lbl">Have BPM</div></div>
  <div class="stat-box"><div class="stat-val">{pct_key}%</div><div class="stat-lbl">Have Key</div></div>
  <div class="stat-box"><div class="stat-val">{pct_genre}%</div><div class="stat-lbl">Have Genre</div></div>
  <div class="stat-box"><div class="stat-val">{pct_emb}%</div><div class="stat-lbl">CLAP Embedded</div></div>
  <div class="stat-box"><div class="stat-val">{n_clusters}</div><div class="stat-lbl">Clusters Found</div></div>
</div>
<div class="grid">
  <div class="panel"><h2>1 · Library Coverage</h2><div id="p1" style="height:260px"></div></div>
  <div class="panel"><h2>2 · Genre Distribution (top 20)</h2><div id="p2" style="height:260px"></div></div>
  <div class="panel"><h2>3 · BPM Distribution</h2><div id="p3" style="height:220px"></div></div>
  <div class="panel"><h2>4 · Camelot Key Usage</h2><div id="p4" style="height:220px"></div></div>
  <div class="panel"><h2>5 · Embedding Cosine Similarity Distribution ({sim_n} pairs sampled)</h2><div id="p5" style="height:240px"></div></div>
  <div class="panel" style="grid-column: 1 / -1;">
    <h2>6 · Cluster Diagnostics</h2>
    {cluster_html}
  </div>
</div>
<script>
const D = {data_json};
const DARK = {{paper_bgcolor:'#161b27',plot_bgcolor:'#161b27',font:{{color:'#ccc',size:11}},margin:{{l:50,r:12,t:10,b:40}}}};

// Panel 1 — coverage bars
Plotly.newPlot('p1', [{{
  type: 'bar', orientation: 'h',
  y: D.cov.fields, x: D.cov.pcts,
  text: D.cov.pcts.map((p,i) => `${{D.cov.counts[i].toLocaleString()}} (${{p}}%)`),
  textposition: 'outside',
  marker: {{color: ['#4363d8','#3cb44b','#f58231','#e6194b','#911eb4']}},
}}], {{...DARK, xaxis:{{range:[0,110],showgrid:false,zeroline:false}}, yaxis:{{automargin:true}}, showlegend:false}}, {{responsive:true,displaylogo:false}});

// Panel 2 — genre bar
Plotly.newPlot('p2', [{{
  type: 'bar',
  x: D.genre.genres, y: D.genre.counts,
  marker: {{color: '#42d4f4', opacity: 0.8}},
}}], {{...DARK, xaxis:{{automargin:true,tickangle:-35}}, yaxis:{{title:'Tracks'}}}}, {{responsive:true,displaylogo:false}});

// Panel 3 — BPM histogram
Plotly.newPlot('p3', [{{
  type: 'histogram', x: D.bpm.bpms, nbinsx: 60,
  marker: {{color: '#f58231', opacity: 0.8}},
}}], {{...DARK, xaxis:{{title:'BPM'}}, yaxis:{{title:'Tracks'}}}}, {{responsive:true,displaylogo:false}});

// Panel 4 — key bar
Plotly.newPlot('p4', [
  {{
    type: 'bar',
    x: D.key.keys.filter((_,i) => i%2===0),
    y: D.key.counts.filter((_,i) => i%2===0),
    name: 'minor (A)', marker: {{color: '#4363d8', opacity: 0.85}},
  }},
  {{
    type: 'bar',
    x: D.key.keys.filter((_,i) => i%2===1),
    y: D.key.counts.filter((_,i) => i%2===1),
    name: 'major (B)', marker: {{color: '#f032e6', opacity: 0.85}},
  }},
], {{...DARK, barmode:'group', xaxis:{{title:'Camelot Position'}}, yaxis:{{title:'Tracks'}}}}, {{responsive:true,displaylogo:false}});

// Panel 5 — sim histogram
Plotly.newPlot('p5', [{{
  type: 'histogram', x: D.sims.sims, nbinsx: 50,
  marker: {{color: '#3cb44b', opacity: 0.8}},
}}], {{
  ...DARK,
  xaxis: {{title:`Cosine Similarity  (mean=${{D.sims.mean}}, std=${{D.sims.std}}, min=${{D.sims.min}})`, range:[0,1]}},
  yaxis: {{title:'Pair count'}},
  shapes: [{{
    type: 'line', x0: D.sims.mean, x1: D.sims.mean, y0: 0, y1: 1,
    yref: 'paper', line: {{color: '#ff6600', dash: 'dot', width: 2}},
  }}],
}}, {{responsive:true,displaylogo:false}});
</script>
</body>
</html>
"""

_CLUSTER_TABLE_HEADER = """
<div style="display:flex;gap:20px;margin-bottom:12px;flex-wrap:wrap;">
  <div class="stat-box"><div class="stat-val">{n_clusters}</div><div class="stat-lbl">Clusters</div></div>
  <div class="stat-box"><div class="stat-val">{n_noise}</div><div class="stat-lbl">Noise tracks</div></div>
  <div class="stat-box"><div class="stat-val">{inter_sim}</div><div class="stat-lbl">Inter-cluster sim (lower=better)</div></div>
</div>
<table class="cluster-table">
<tr><th>#</th><th>Size</th><th>Intra-sim (↑good)</th><th>Dominant Genre</th><th>Genre purity</th><th>BPM mean±std</th></tr>
"""

_CLUSTER_ROW = """<tr class="{cls}"><td>{name}</td><td>{size}</td><td>{intra}</td><td>{genre}</td><td>{purity}</td><td>{bpm}</td></tr>"""

_CLUSTER_UNAVAILABLE = "<p style='color:#666'>{reason}</p>"


def _render_cluster_html(clust: dict) -> str:
    if not clust.get("available"):
        return _CLUSTER_UNAVAILABLE.format(reason=clust.get("reason", "unavailable"))
    header = _CLUSTER_TABLE_HEADER.format(
        n_clusters=clust["n_clusters"],
        n_noise=clust["n_noise"],
        inter_sim=clust.get("inter_cluster_mean_sim", "n/a"),
    )
    rows = ""
    for c in sorted(clust["clusters"], key=lambda x: (-x["size"])):
        bpm_str = (
            f"{c['bpm_mean']} ± {c['bpm_std']}" if c.get("bpm_mean") else "?"
        )
        rows += _CLUSTER_ROW.format(
            cls="noise-row" if c["label"] == -1 else "",
            name=c["name"],
            size=c["size"],
            intra=c["intra_sim"],
            genre=c["dominant_genre"],
            purity=f"{c['genre_purity']}%",
            bpm=bpm_str,
        )
    return header + rows + "</table>"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="MultiDJ diagnostic dashboard")
    parser.add_argument("--db", default=None)
    parser.add_argument("--out", default="diagnostics.html")
    parser.add_argument("--sample", type=int, default=400,
                        help="Number of embeddings to sample for similarity analysis (default: 400)")
    parser.add_argument("--model", default="laion/larger_clap_music",
                        help="Embedding model name (default: laion/larger_clap_music)")
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

    print(f"DB: {db_path}", file=sys.stderr)
    conn = _connect(db_path)

    print("Loading tracks…", file=sys.stderr)
    tracks = _load_library(conn)
    n = len(tracks)
    print(f"  {n} active tracks", file=sys.stderr)

    print(f"Loading embeddings (model={args.model}, sample={args.sample})…", file=sys.stderr)
    all_ids, all_vecs = _load_embeddings(conn, args.model, sample=None)
    n_embedded = len(all_ids)
    print(f"  {n_embedded} embeddings", file=sys.stderr)

    sample_ids, sample_vecs = all_ids[:args.sample], all_vecs[:args.sample] if len(all_vecs) else all_vecs

    # Build panels
    print("Computing panels…", file=sys.stderr)
    cov = _coverage_data(tracks, n_embedded)
    genre = _genre_data(tracks)
    bpm = _bpm_data(tracks)
    key = _key_data(tracks)

    print(f"  Similarity distribution ({len(sample_ids)} embeddings)…", file=sys.stderr)
    sims = _sim_distribution(sample_vecs) if len(sample_vecs) >= 2 else {"sims": [], "mean": 0, "std": 0, "min": 0, "max": 0, "n": 0}

    print("  Cluster diagnostics…", file=sys.stderr)
    clust = _cluster_diagnostics(all_ids, all_vecs, conn, tracks)
    print(f"  → {clust.get('n_clusters', 0)} clusters, {clust.get('n_noise', 0)} noise", file=sys.stderr)

    conn.close()

    # Coverage quick stats
    def _pct(count):
        return round(count / n * 100, 1) if n else 0

    pct_fields = {f: v for f, v in zip(cov["fields"], cov["pcts"])}
    subtitle = (f"{n:,} active tracks · {args.model} · "
                f"sample={len(sample_ids)} embeddings for similarity panel")

    data = {"cov": cov, "genre": genre, "bpm": bpm, "key": key, "sims": sims}

    html = _HTML.format(
        subtitle=subtitle,
        total=f"{n:,}",
        pct_bpm=pct_fields.get("BPM", 0),
        pct_key=pct_fields.get("Key", 0),
        pct_genre=pct_fields.get("Genre", 0),
        pct_emb=pct_fields.get("Embedding (CLAP)", 0),
        n_clusters=clust.get("n_clusters", "?") if clust.get("available") else "?",
        sim_n=f"{sims['n']:,}",
        cluster_html=_render_cluster_html(clust),
        data_json=json.dumps(data),
    )

    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"\nDiagnostics written to: {out.resolve()}", file=sys.stderr)
    print(f"Open in browser: file://{out.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
