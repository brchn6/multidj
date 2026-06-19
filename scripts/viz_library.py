#!/usr/bin/env python3
"""Interactive 2D library visualization for MultiDJ.

Produces a self-contained HTML scatter plot — one point per track —
useful for evaluating clustering quality before committing to a full
embedding run.

Two layout modes are supported and automatically chosen:
  • embedding  — UMAP on CLAP/CLaMP3 embedding vectors (richest view;
                 requires at least 30 embedded tracks)
  • metadata   — UMAP on [BPM, key_x, key_y, key_mode] when embeddings
                 are sparse or absent.  Works with BPM + Camelot key only.

In both cases the output is the same interactive Plotly HTML file:
  - One dot per track
  - Color = genre (with fallback to folder name if no genre)
  - Hover = artist · title · BPM · key · genre
  - Three color-toggle buttons: Genre / BPM / Key
  - BPM range slider to narrow down the view

Usage:
    python scripts/viz_library.py [--db PATH] [--out PATH] [--mode auto|embedding|metadata]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Key → circle-of-fifths encoding
# ---------------------------------------------------------------------------
# Semitone offset from C for each pitch class (circle of fifths order)
_FIFTHS_ORDER = ["C", "G", "D", "A", "E", "B", "F#", "Db", "Ab", "Eb", "Bb", "F"]
_PITCH_POS: dict[str, int] = {k: i for i, k in enumerate(_FIFTHS_ORDER)}
# Extra enharmonic aliases
_PITCH_ALIASES = {
    "Gb": 6, "C#": 7, "G#": 8, "D#": 9, "A#": 10, "Cb": 11,
}
_PITCH_POS.update(_PITCH_ALIASES)

_CAMELOT_RE = re.compile(r"^(\d{1,2})([AB])$", re.IGNORECASE)

_KEY_RE = re.compile(
    r"^([A-G][b#]?)\s*(min|maj|m|M)?$",
    re.IGNORECASE,
)


def _key_to_features(key: str | None) -> tuple[float, float, float] | None:
    """Convert any key string to (x, y, mode) on the unit circle of fifths.

    Accepts:
      • Musical notation: Gmin, F#min, Am, C, Dmaj, Bb, D#min
      • Camelot wheel:    9B, 1A, 12B
    Returns (cos_angle, sin_angle, mode) where mode=1 for major, 0 for minor.
    """
    if not key:
        return None
    k = key.strip()

    # Camelot format
    m = _CAMELOT_RE.match(k)
    if m:
        n = int(m.group(1))
        ab = m.group(2).upper()
        angle = 2 * math.pi * (n - 1) / 12
        return (math.cos(angle), math.sin(angle), 1.0 if ab == "B" else 0.0)

    # Musical notation
    m2 = _KEY_RE.match(k)
    if not m2:
        return None
    root = m2.group(1)
    suffix = (m2.group(2) or "").lower()
    pos = _PITCH_POS.get(root)
    if pos is None:
        return None
    is_minor = suffix in ("min", "m") or (suffix == "" and root[0].islower())
    angle = 2 * math.pi * pos / 12
    return (math.cos(angle), math.sin(angle), 0.0 if is_minor else 1.0)


# Keep old name for compatibility
_camelot_to_features = _key_to_features


# ---------------------------------------------------------------------------
# Colour palettes
# ---------------------------------------------------------------------------
_GENRE_PALETTE = [
    "#e6194b","#3cb44b","#4363d8","#f58231","#911eb4",
    "#42d4f4","#f032e6","#bfef45","#fabed4","#469990",
    "#dcbeff","#9A6324","#fffac8","#800000","#aaffc3",
    "#808000","#ffd8b1","#000075","#a9a9a9","#ffffff",
    "#000000","#e6beff","#ffe119","#008080","#ff6600",
]


def _genre_colors(genres: list[str]) -> dict[str, str]:
    unique = sorted(set(g for g in genres if g))
    return {g: _GENRE_PALETTE[i % len(_GENRE_PALETTE)] for i, g in enumerate(unique)}


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------

def _load_tracks(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            t.id, t.artist, t.title, t.genre, t.bpm, t.key, t.energy, t.path,
            e.vector, e.model_name
        FROM tracks t
        LEFT JOIN embeddings e ON t.id = e.track_id
        WHERE t.deleted = 0
        ORDER BY t.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _folder_label(path: str, music_root: str = "") -> str:
    """Extract top-level folder from path as a fallback genre label."""
    p = Path(path)
    try:
        parts = p.relative_to(music_root).parts if music_root else p.parts
    except ValueError:
        parts = p.parts
    return parts[0] if parts else ""


# ---------------------------------------------------------------------------
# Feature matrix builder
# ---------------------------------------------------------------------------

def _build_features_metadata(tracks: list[dict]) -> tuple[list[dict], list[list[float]]]:
    """Build feature matrix from BPM + Camelot key. Skips tracks missing both."""
    good, feats = [], []
    for t in tracks:
        kf = _key_to_features(t.get("key"))
        bpm = t.get("bpm")
        if not kf or not bpm:
            continue
        bpm_norm = float(bpm) / 200.0
        feats.append([bpm_norm, kf[0], kf[1], kf[2]])
        good.append(t)
    return good, feats


def _build_features_embeddings(tracks: list[dict]) -> tuple[list[dict], list]:
    """Use stored embedding vectors. Returns only tracks with valid embeddings."""
    import numpy as np
    good, vecs = [], []
    for t in tracks:
        blob = t.get("vector")
        if blob is None:
            continue
        v = np.frombuffer(blob, dtype=np.float32).copy()
        vecs.append(v)
        good.append(t)
    return good, vecs


# ---------------------------------------------------------------------------
# UMAP projection
# ---------------------------------------------------------------------------

def _umap_2d(matrix, n_neighbors: int = 15, min_dist: float = 0.1,
             metric: str = "euclidean") -> tuple[list[float], list[float]]:
    import numpy as np
    import umap  # type: ignore

    arr = np.array(matrix, dtype=np.float32)
    n = len(arr)
    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=min(n_neighbors, n - 1),
        min_dist=min_dist,
        metric=metric,
        random_state=42,
    )
    xy = reducer.fit_transform(arr)
    return xy[:, 0].tolist(), xy[:, 1].tolist()


# ---------------------------------------------------------------------------
# HTML generator
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MultiDJ Library — {title}</title>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
  <style>
    body {{ margin: 0; background: #111; color: #eee; font-family: monospace; }}
    #header {{ padding: 12px 20px; background: #1a1a2e; border-bottom: 1px solid #333; }}
    #header h1 {{ margin: 0; font-size: 1.1rem; color: #a0c4ff; }}
    #header p  {{ margin: 4px 0 0; font-size: 0.8rem; color: #888; }}
    #controls {{ padding: 8px 20px; background: #16213e; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }}
    #controls label {{ font-size: 0.8rem; color: #aaa; }}
    #controls select, #controls input {{ background: #222; color: #eee; border: 1px solid #444; padding: 3px 8px; border-radius: 4px; }}
    #plot  {{ width: 100%; height: calc(100vh - 110px); }}
    .btn  {{ background: #1e3a5f; color: #a0c4ff; border: 1px solid #3a6ea8; padding: 4px 12px;
              border-radius: 4px; cursor: pointer; font-size: 0.8rem; }}
    .btn:hover {{ background: #2a4f7f; }}
  </style>
</head>
<body>
<div id="header">
  <h1>MultiDJ Library Visualization — {title}</h1>
  <p>{subtitle}</p>
</div>
<div id="controls">
  <label>Color by:
    <select id="colorMode" onchange="recolor()">
      <option value="genre">Genre</option>
      <option value="bpm">BPM</option>
      <option value="key">Key</option>
    </select>
  </label>
  <label>BPM min: <input type="number" id="bpmMin" value="{bpm_min}" step="5" style="width:60px" onchange="applyFilter()"></label>
  <label>BPM max: <input type="number" id="bpmMax" value="{bpm_max}" step="5" style="width:60px" onchange="applyFilter()"></label>
  <button class="btn" onclick="resetFilter()">Reset filter</button>
  <label>Search: <input type="text" id="search" placeholder="artist or title…" style="width:180px" oninput="applyFilter()"></label>
  <span id="count" style="font-size:0.8rem;color:#888"></span>
</div>
<div id="plot"></div>

<script>
const RAW = {data_json};

// Build a flat array of point indices grouped by genre for Plotly traces
function buildTraces(indices, colorMode) {{
  if (colorMode === 'genre') {{
    // one trace per genre
    const byGenre = {{}};
    indices.forEach(i => {{
      const g = RAW.genre[i] || '(no genre)';
      if (!byGenre[g]) byGenre[g] = [];
      byGenre[g].push(i);
    }});
    return Object.entries(byGenre).sort().map(([g, idxs]) => ({{
      type: 'scattergl', mode: 'markers', name: g,
      x: idxs.map(i => RAW.x[i]), y: idxs.map(i => RAW.y[i]),
      text: idxs.map(i => RAW.label[i]),
      hoverinfo: 'text+name',
      marker: {{ size: 5, opacity: 0.75 }},
    }}));
  }} else {{
    // single trace with continuous color
    const vals = indices.map(i => colorMode === 'bpm' ? RAW.bpm[i] : RAW.key_num[i]);
    return [{{
      type: 'scattergl', mode: 'markers', name: colorMode,
      x: indices.map(i => RAW.x[i]), y: indices.map(i => RAW.y[i]),
      text: indices.map(i => RAW.label[i]),
      hoverinfo: 'text',
      marker: {{
        size: 5, opacity: 0.75,
        color: vals,
        colorscale: colorMode === 'bpm' ? 'Viridis' : 'HSV',
        showscale: true,
        colorbar: {{ title: colorMode === 'bpm' ? 'BPM' : 'Key (Camelot)', thickness: 12, len: 0.7 }},
      }},
    }}];
  }}
}}

const layout = {{
  paper_bgcolor: '#111', plot_bgcolor: '#111',
  font: {{ color: '#ccc', size: 11 }},
  xaxis: {{ showgrid: false, zeroline: false, showticklabels: false }},
  yaxis: {{ showgrid: false, zeroline: false, showticklabels: false }},
  margin: {{ l: 10, r: 10, t: 10, b: 10 }},
  legend: {{ bgcolor: '#1a1a1a', bordercolor: '#444', borderwidth: 1, font: {{ size: 10 }} }},
  hovermode: 'closest',
}};

const config = {{ responsive: true, displaylogo: false,
  modeBarButtonsToRemove: ['toImage'] }};

let activeIndices = RAW.x.map((_, i) => i);

function render(indices) {{
  const mode = document.getElementById('colorMode').value;
  const traces = buildTraces(indices, mode);
  Plotly.react('plot', traces, layout, config);
  document.getElementById('count').textContent = indices.length + ' tracks shown';
}}

function recolor() {{ render(activeIndices); }}

function applyFilter() {{
  const bmin = parseFloat(document.getElementById('bpmMin').value) || 0;
  const bmax = parseFloat(document.getElementById('bpmMax').value) || 9999;
  const q = document.getElementById('search').value.toLowerCase();
  activeIndices = RAW.x.map((_, i) => i).filter(i => {{
    const bpm = RAW.bpm[i] || 0;
    if (bpm < bmin || bpm > bmax) return false;
    if (q && !RAW.search[i].includes(q)) return false;
    return true;
  }});
  render(activeIndices);
}}

function resetFilter() {{
  document.getElementById('bpmMin').value = {bpm_min};
  document.getElementById('bpmMax').value = {bpm_max};
  document.getElementById('search').value = '';
  activeIndices = RAW.x.map((_, i) => i);
  render(activeIndices);
}}

render(activeIndices);
</script>
</body>
</html>
"""


def _build_data_json(tracks: list[dict], xs: list[float], ys: list[float]) -> str:
    music_root = "/home/barc/Weizmann Institute Dropbox/Bar Cohen/Music"

    def label(t: dict) -> str:
        a = t.get("artist") or ""
        ti = t.get("title") or Path(t["path"]).stem
        bpm = t.get("bpm") or "?"
        key = t.get("key") or "?"
        g = t.get("genre") or _folder_label(t["path"], music_root) or "?"
        model = t.get("model_name") or "metadata"
        return f"<b>{a} — {ti}</b><br>BPM: {bpm}  Key: {key}  Genre: {g}<br><i>{model}</i>"

    def key_num(k):
        f = _key_to_features(k)
        if not f:
            return 0
        # Map angle back to 0–12 for colorscale
        angle = math.atan2(f[1], f[0])
        return (angle / (2 * math.pi) * 12) % 12

    genre_list = []
    for t in tracks:
        g = t.get("genre")
        if not g:
            g = _folder_label(t["path"], music_root)
        genre_list.append(g or "")

    bpms = [float(t.get("bpm") or 0) for t in tracks]

    payload = {
        "x": xs,
        "y": ys,
        "genre": genre_list,
        "bpm": bpms,
        "key_num": [key_num(t.get("key")) for t in tracks],
        "label": [label(t) for t in tracks],
        "search": [
            ((t.get("artist") or "") + " " + (t.get("title") or "")).lower()
            for t in tracks
        ],
    }
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate interactive 2D library viz")
    parser.add_argument("--db", default=None, help="MultiDJ DB path")
    parser.add_argument("--out", default="library_viz.html", help="Output HTML file")
    parser.add_argument("--mode", choices=["auto", "embedding", "metadata"],
                        default="auto", help="Layout mode")
    parser.add_argument("--model", default=None,
                        help="Embedding model name filter (e.g. laion/larger_clap_music)")
    args = parser.parse_args()

    # Resolve DB path
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

    print(f"Loading tracks from: {db_path}", file=sys.stderr)
    tracks = _load_tracks(db_path)
    print(f"  {len(tracks)} active tracks loaded", file=sys.stderr)

    # Filter by model if specified
    if args.model:
        tracks = [t for t in tracks if t.get("model_name") == args.model]

    # Count valid embeddings
    embedded = [t for t in tracks if t.get("vector") is not None]
    print(f"  {len(embedded)} tracks have embeddings", file=sys.stderr)

    # Decide mode
    mode = args.mode
    if mode == "auto":
        mode = "embedding" if len(embedded) >= 30 else "metadata"
    print(f"  Layout mode: {mode}", file=sys.stderr)

    if mode == "embedding":
        good, matrix = _build_features_embeddings(tracks)
        metric = "cosine"
        title = f"UMAP on audio embeddings ({len(good)} tracks)"
        subtitle = f"Model: {good[0].get('model_name', '?') if good else '?'} · colored by genre"
    else:
        good, matrix = _build_features_metadata(tracks)
        metric = "euclidean"
        title = f"UMAP on BPM + Key ({len(good)} tracks)"
        subtitle = "Colored by genre  ·  metadata-only view (no embeddings used)"

    print(f"  Running UMAP on {len(good)} points…", file=sys.stderr)
    xs, ys = _umap_2d(matrix, metric=metric)

    bpms = [float(t.get("bpm") or 0) for t in good]
    bpm_min = int(min(b for b in bpms if b > 0)) if any(b > 0 for b in bpms) else 60
    bpm_max = int(max(bpms)) if bpms else 200

    data_json = _build_data_json(good, xs, ys)
    html = _HTML_TEMPLATE.format(
        title=title,
        subtitle=subtitle,
        data_json=data_json,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
    )

    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"\nViz written to: {out.resolve()}", file=sys.stderr)
    print(f"Open in browser: file://{out.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
