#!/usr/bin/env python3
"""Interactive 2D library visualization for MultiDJ.

Produces a self-contained HTML scatter plot — one point per track —
useful for evaluating clustering quality and getting DJ set suggestions.

Two layout modes:
  • embedding  — UMAP on CLAP/CLaMP3 embedding vectors (richest view;
                 requires at least 30 embedded tracks)
  • metadata   — UMAP on [BPM, key_x, key_y, key_mode] when embeddings
                 are sparse or absent.

Interactive features:
  - Color toggle: Genre / BPM / Key / Cluster
  - BPM range slider filter
  - Text search (artist or title)
  - Click any point → sidebar shows top-5 nearest neighbors with BPM/key/cluster info
    (neighbors precomputed in Python for instant JS lookup)

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
_FIFTHS_ORDER = ["C", "G", "D", "A", "E", "B", "F#", "Db", "Ab", "Eb", "Bb", "F"]
_PITCH_POS: dict[str, int] = {k: i for i, k in enumerate(_FIFTHS_ORDER)}
_PITCH_ALIASES = {
    "Gb": 6, "C#": 7, "G#": 8, "D#": 9, "A#": 10, "Cb": 11,
}
_PITCH_POS.update(_PITCH_ALIASES)

_CAMELOT_RE = re.compile(r"^(\d{1,2})([AB])$", re.IGNORECASE)
_KEY_RE = re.compile(r"^([A-G][b#]?)\s*(min|maj|m|M)?$", re.IGNORECASE)


def _key_to_features(key: str | None) -> tuple[float, float, float] | None:
    if not key:
        return None
    k = key.strip()
    m = _CAMELOT_RE.match(k)
    if m:
        n = int(m.group(1))
        ab = m.group(2).upper()
        angle = 2 * math.pi * (n - 1) / 12
        return (math.cos(angle), math.sin(angle), 1.0 if ab == "B" else 0.0)
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

# Cluster palette — distinct enough to tell apart; -1 (noise) → grey
_CLUSTER_PALETTE = [
    "#e6194b","#3cb44b","#4363d8","#f58231","#911eb4","#42d4f4",
    "#f032e6","#bfef45","#469990","#9A6324","#800000","#aaffc3",
    "#808000","#ffd8b1","#000075","#ffe119","#008080","#ff6600",
    "#dcbeff","#fabed4","#a9a9a9",
]


def _genre_colors(genres: list[str]) -> dict[str, str]:
    unique = sorted(set(g for g in genres if g))
    return {g: _GENRE_PALETTE[i % len(_GENRE_PALETTE)] for i, g in enumerate(unique)}


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------

def _load_tracks(db_path: str, model_filter: str | None = None) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # When multiple embeddings exist per track, pick the most recently stored one
    if model_filter:
        rows = conn.execute("""
            SELECT
                t.id, t.artist, t.title, t.genre, t.bpm, t.key, t.energy, t.path,
                e.vector, e.model_name
            FROM tracks t
            LEFT JOIN embeddings e ON t.id = e.track_id AND e.model_name = ?
            WHERE t.deleted = 0
            ORDER BY t.id
        """, (model_filter,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT
                t.id, t.artist, t.title, t.genre, t.bpm, t.key, t.energy, t.path,
                e.vector, e.model_name
            FROM tracks t
            LEFT JOIN (
                SELECT track_id, vector, model_name
                FROM embeddings
                GROUP BY track_id
                HAVING created_at = MAX(created_at)
            ) e ON t.id = e.track_id
            WHERE t.deleted = 0
            ORDER BY t.id
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _folder_label(path: str, music_root: str = "") -> str:
    p = Path(path)
    try:
        parts = p.relative_to(music_root).parts if music_root else p.parts
    except ValueError:
        parts = p.parts
    return parts[0] if parts else ""


# ---------------------------------------------------------------------------
# Feature matrix builders
# ---------------------------------------------------------------------------

def _build_features_metadata(tracks: list[dict]) -> tuple[list[dict], list[list[float]]]:
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
# Clustering (inline HDBSCAN on embedding vectors)
# ---------------------------------------------------------------------------

def _cluster_labels(matrix, min_cluster_size: int = 10) -> list[int]:
    """UMAP (512d→10d, cosine) then HDBSCAN. Returns per-point labels (-1 = noise).

    Mirrors the logic in multidj/cluster.py so that visualization clusters
    match what `multidj cluster vibe` would produce.
    """
    import numpy as np
    try:
        import umap  # type: ignore
        import hdbscan  # type: ignore
    except ImportError:
        print("umap/hdbscan not available — cluster coloring disabled", file=sys.stderr)
        return [-1] * len(matrix)

    arr = np.array(matrix, dtype=np.float32)
    n = len(arr)
    n_components = min(10, max(2, n - 2))

    print(f"    UMAP {arr.shape[1]}d → {n_components}d (cosine)…", file=sys.stderr)
    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=min(15, n - 1),
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    reduced = reducer.fit_transform(arr)

    mcs = max(min_cluster_size, max(5, n // 80))
    print(f"    HDBSCAN (min_cluster_size={mcs})…", file=sys.stderr)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=mcs,
        min_samples=3,
        metric="euclidean",
    )
    labels = clusterer.fit_predict(reduced)
    return labels.tolist()


# ---------------------------------------------------------------------------
# Nearest-neighbour precomputation
# ---------------------------------------------------------------------------

def _precompute_neighbors(matrix, k: int = 5) -> list[list[int]]:
    """For each point, return indices of its k nearest neighbours (by cosine sim)."""
    import numpy as np

    arr = np.array(matrix, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
    arr_norm = arr / norms

    n = len(arr_norm)
    neighbors: list[list[int]] = []
    # Batch: compute full similarity matrix then argsort.
    # For ≤5000 tracks this fits comfortably in RAM (~100 MB for 3500×512 fp32).
    sims = arr_norm @ arr_norm.T  # (n, n)
    np.fill_diagonal(sims, -1.0)   # exclude self

    for i in range(n):
        top = np.argsort(-sims[i])[:k]
        neighbors.append(top.tolist())
    return neighbors


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
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #111; color: #eee; font-family: monospace; display: flex; flex-direction: column; height: 100vh; }}
    #header {{ padding: 10px 16px; background: #1a1a2e; border-bottom: 1px solid #333; flex-shrink: 0; }}
    #header h1 {{ margin: 0; font-size: 1.05rem; color: #a0c4ff; }}
    #header p  {{ margin: 3px 0 0; font-size: 0.78rem; color: #888; }}
    #controls {{ padding: 6px 16px; background: #16213e; display: flex; gap: 14px; align-items: center;
                 flex-wrap: wrap; flex-shrink: 0; border-bottom: 1px solid #222; }}
    #controls label {{ font-size: 0.78rem; color: #aaa; }}
    #controls select, #controls input[type=number], #controls input[type=text] {{
      background: #222; color: #eee; border: 1px solid #444; padding: 2px 6px; border-radius: 4px; }}
    #main {{ display: flex; flex: 1; overflow: hidden; }}
    #plot  {{ flex: 1; min-width: 0; }}
    #sidebar {{
      width: 300px; min-width: 300px; background: #161b27; border-left: 1px solid #333;
      display: flex; flex-direction: column; overflow: hidden; transition: width 0.2s;
    }}
    #sidebar.collapsed {{ width: 0; min-width: 0; border-left: none; }}
    #sidebar-header {{
      padding: 10px 14px 6px; border-bottom: 1px solid #2a3550; font-size: 0.78rem;
      color: #a0c4ff; display: flex; justify-content: space-between; align-items: center;
    }}
    #sidebar-content {{ padding: 10px 14px; overflow-y: auto; flex: 1; font-size: 0.76rem; }}
    .track-card {{
      background: #1e2942; border: 1px solid #2a3f6a; border-radius: 6px;
      padding: 8px 10px; margin-bottom: 8px; cursor: pointer;
    }}
    .track-card:hover {{ background: #243252; border-color: #4a6fa8; }}
    .track-card.highlighted {{ border-color: #ffd700; background: #2a3520; }}
    .track-title {{ color: #e0e0ff; font-weight: bold; font-size: 0.8rem; line-height: 1.3; }}
    .track-meta  {{ color: #888; margin-top: 3px; }}
    .track-score {{ color: #a0c4ff; float: right; font-size: 0.72rem; margin-top: 2px; }}
    .cluster-badge {{
      display: inline-block; padding: 1px 6px; border-radius: 3px;
      font-size: 0.68rem; margin-top: 3px; color: #111; font-weight: bold;
    }}
    .btn {{ background: #1e3a5f; color: #a0c4ff; border: 1px solid #3a6ea8; padding: 3px 10px;
             border-radius: 4px; cursor: pointer; font-size: 0.78rem; }}
    .btn:hover {{ background: #2a4f7f; }}
    #count {{ font-size: 0.78rem; color: #888; }}
    #sidebar-query {{ color: #ffd700; font-weight: bold; margin-bottom: 8px; font-size: 0.8rem; }}
    #sidebar-cluster {{ color: #88c0d0; font-size: 0.72rem; margin-bottom: 10px; }}
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
      <option value="cluster">Cluster</option>
      <option value="bpm">BPM</option>
      <option value="key">Key</option>
    </select>
  </label>
  <label>BPM min: <input type="number" id="bpmMin" value="{bpm_min}" step="5" style="width:56px" onchange="applyFilter()"></label>
  <label>BPM max: <input type="number" id="bpmMax" value="{bpm_max}" step="5" style="width:56px" onchange="applyFilter()"></label>
  <button class="btn" onclick="resetFilter()">Reset</button>
  <label>Search: <input type="text" id="search" placeholder="artist or title…" style="width:170px" oninput="applyFilter()"></label>
  <span id="count"></span>
</div>
<div id="main">
  <div id="plot"></div>
  <div id="sidebar">
    <div id="sidebar-header">
      <span>Suggestions</span>
      <button class="btn" onclick="closeSidebar()" style="padding:1px 8px;font-size:0.7rem">✕</button>
    </div>
    <div id="sidebar-content">
      <div style="color:#555;font-size:0.75rem">Click any track on the map to see<br>its nearest neighbors here.</div>
    </div>
  </div>
</div>

<script>
const RAW = {data_json};

const CLUSTER_COLORS = {cluster_colors_json};

// ── Build Plotly traces ───────────────────────────────────────────────────
function buildTraces(indices, colorMode) {{
  if (colorMode === 'genre') {{
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
      customdata: idxs,
      hoverinfo: 'text+name',
      marker: {{ size: 5, opacity: 0.75 }},
    }}));
  }} else if (colorMode === 'cluster') {{
    const byCluster = {{}};
    indices.forEach(i => {{
      const c = String(RAW.cluster[i] !== undefined ? RAW.cluster[i] : -1);
      if (!byCluster[c]) byCluster[c] = [];
      byCluster[c].push(i);
    }});
    return Object.entries(byCluster).sort((a,b) => parseInt(a[0]) - parseInt(b[0])).map(([c, idxs]) => {{
      const label = c === '-1' ? 'Noise' : `Cluster ${{c}}`;
      const color = CLUSTER_COLORS[c] || '#555';
      return {{
        type: 'scattergl', mode: 'markers', name: label,
        x: idxs.map(i => RAW.x[i]), y: idxs.map(i => RAW.y[i]),
        text: idxs.map(i => RAW.label[i]),
        customdata: idxs,
        hoverinfo: 'text+name',
        marker: {{ size: 5, opacity: 0.80, color }},
      }};
    }});
  }} else {{
    const vals = indices.map(i => colorMode === 'bpm' ? RAW.bpm[i] : RAW.key_num[i]);
    return [{{
      type: 'scattergl', mode: 'markers', name: colorMode,
      x: indices.map(i => RAW.x[i]), y: indices.map(i => RAW.y[i]),
      text: indices.map(i => RAW.label[i]),
      customdata: indices,
      hoverinfo: 'text',
      marker: {{
        size: 5, opacity: 0.75,
        color: vals,
        colorscale: colorMode === 'bpm' ? 'Viridis' : 'HSV',
        showscale: true,
        colorbar: {{ title: colorMode === 'bpm' ? 'BPM' : 'Key', thickness: 12, len: 0.7 }},
      }},
    }}];
  }}
}}

// ── Highlight overlay (selected + its neighbors) ──────────────────────────
let highlightDiv = null;

function _removeHighlight() {{
  const el = document.getElementById('highlight-overlay');
  if (el) el.remove();
}}

function showHighlight(selIdx, neighborIdxs) {{
  _removeHighlight();
  const allIdxs = [selIdx, ...neighborIdxs];
  const allX = allIdxs.map(i => RAW.x[i]);
  const allY = allIdxs.map(i => RAW.y[i]);
  const markers = allIdxs.map((i, pos) => pos === 0
    ? {{ symbol: 'star', size: 14, color: '#ffd700', line: {{ color: '#fff', width: 1 }} }}
    : {{ symbol: 'circle', size: 9,  color: '#ff8c00', line: {{ color: '#fff', width: 1 }} }}
  );
  // Plotly addTraces approach
  const overlayTrace = {{
    type: 'scattergl', mode: 'markers',
    x: allX, y: allY,
    text: allIdxs.map(i => RAW.label[i]),
    hoverinfo: 'text',
    showlegend: false,
    marker: {{
      symbol: allIdxs.map((i, pos) => pos === 0 ? 'star' : 'circle-open'),
      size:   allIdxs.map((i, pos) => pos === 0 ? 14 : 9),
      color:  allIdxs.map((i, pos) => pos === 0 ? '#ffd700' : '#ff8c00'),
      line: {{ color: '#fff', width: allIdxs.map(() => 1) }},
    }},
    name: '_highlight',
  }};
  Plotly.addTraces('plot', overlayTrace);
}}

function clearHighlight() {{
  const plotEl = document.getElementById('plot');
  if (!plotEl || !plotEl.data) return;
  const idx = plotEl.data.findIndex(t => t.name === '_highlight');
  if (idx >= 0) Plotly.deleteTraces('plot', idx);
}}

// ── Sidebar ───────────────────────────────────────────────────────────────
function closeSidebar() {{
  document.getElementById('sidebar').classList.add('collapsed');
  clearHighlight();
}}

function openSidebar(globalIdx) {{
  const sidebar = document.getElementById('sidebar');
  sidebar.classList.remove('collapsed');

  const artist = RAW.artist[globalIdx] || '';
  const title  = RAW.title_str[globalIdx] || '';
  const bpm    = RAW.bpm[globalIdx] || '?';
  const key    = RAW.key_str[globalIdx] || '?';
  const cluster = RAW.cluster[globalIdx] !== undefined ? RAW.cluster[globalIdx] : -1;
  const clusterLabel = cluster === -1 ? 'Noise' : `Cluster ${{cluster}}`;
  const clusterColor = CLUSTER_COLORS[String(cluster)] || '#555';

  const content = document.getElementById('sidebar-content');
  const neighbors = RAW.neighbors[globalIdx] || [];

  let html = `<div id="sidebar-query"><span style="color:#ffd700">▶ Now:</span> ${{artist}} — ${{title}}</div>`;
  html += `<div id="sidebar-cluster">`;
  html += `BPM <b>${{bpm}}</b> · Key <b>${{key}}</b> · <span class="cluster-badge" style="background:${{clusterColor}}">${{clusterLabel}}</span>`;
  html += `</div>`;
  html += `<div style="color:#a0c4ff;font-size:0.72rem;margin-bottom:6px">Top ${{neighbors.length}} nearest neighbors:</div>`;

  neighbors.forEach((ni, rank) => {{
    const na = RAW.artist[ni] || '';
    const nt = RAW.title_str[ni] || '';
    const nb = RAW.bpm[ni] || '?';
    const nk = RAW.key_str[ni] || '?';
    const nc = RAW.cluster[ni] !== undefined ? RAW.cluster[ni] : -1;
    const ncLabel = nc === -1 ? 'Noise' : `Cluster ${{nc}}`;
    const ncColor = CLUSTER_COLORS[String(nc)] || '#555';
    const sim = RAW.neighbor_sims[globalIdx] ? RAW.neighbor_sims[globalIdx][rank].toFixed(3) : '';

    html += `<div class="track-card" onclick="focusNeighbor(${{ni}})">`;
    html += `<div class="track-score">sim ${{sim}}</div>`;
    html += `<div class="track-title">${{rank+1}}. ${{na}} — ${{nt}}</div>`;
    html += `<div class="track-meta">BPM ${{nb}} · Key ${{nk}}</div>`;
    html += `<span class="cluster-badge" style="background:${{ncColor}}">${{ncLabel}}</span>`;
    html += `</div>`;
  }});

  content.innerHTML = html;
  clearHighlight();
  showHighlight(globalIdx, neighbors);
}}

function focusNeighbor(ni) {{
  // Pan/zoom to the neighbor point — just re-highlight with it as center
  clearHighlight();
  showHighlight(ni, RAW.neighbors[ni] || []);
  openSidebar(ni);
}}

// ── Layout & config ───────────────────────────────────────────────────────
const layout = {{
  paper_bgcolor: '#111', plot_bgcolor: '#111',
  font: {{ color: '#ccc', size: 11 }},
  xaxis: {{ showgrid: false, zeroline: false, showticklabels: false }},
  yaxis: {{ showgrid: false, zeroline: false, showticklabels: false }},
  margin: {{ l: 10, r: 10, t: 10, b: 10 }},
  legend: {{ bgcolor: '#1a1a1a', bordercolor: '#444', borderwidth: 1, font: {{ size: 10 }} }},
  hovermode: 'closest',
}};

const config = {{
  responsive: true, displaylogo: false,
  modeBarButtonsToRemove: ['toImage'],
}};

let activeIndices = RAW.x.map((_, i) => i);

function render(indices) {{
  const mode = document.getElementById('colorMode').value;
  const traces = buildTraces(indices, mode);
  Plotly.react('plot', traces, layout, config).then(() => {{
    // Re-attach click handler after re-render
    document.getElementById('plot').on('plotly_click', onPlotClick);
  }});
  document.getElementById('count').textContent = indices.length + ' tracks shown';
}}

function onPlotClick(eventData) {{
  if (!eventData || !eventData.points || !eventData.points.length) return;
  const pt = eventData.points[0];
  // customdata holds the global RAW index
  const globalIdx = pt.customdata;
  if (globalIdx === undefined || globalIdx === null) return;
  openSidebar(globalIdx);
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
  clearHighlight();
  render(activeIndices);
}}

function resetFilter() {{
  document.getElementById('bpmMin').value = {bpm_min};
  document.getElementById('bpmMax').value = {bpm_max};
  document.getElementById('search').value = '';
  activeIndices = RAW.x.map((_, i) => i);
  clearHighlight();
  render(activeIndices);
}}

render(activeIndices);
</script>
</body>
</html>
"""


def _build_data_json(
    tracks: list[dict],
    xs: list[float],
    ys: list[float],
    cluster_labels: list[int],
    neighbors: list[list[int]],
    neighbor_sims: list[list[float]],
) -> str:
    music_root = "/home/barc/Weizmann Institute Dropbox/Bar Cohen/Music"

    def label(t: dict, cl: int) -> str:
        a = t.get("artist") or ""
        ti = t.get("title") or Path(t["path"]).stem
        bpm = t.get("bpm") or "?"
        key = t.get("key") or "?"
        g = t.get("genre") or _folder_label(t["path"], music_root) or "?"
        cl_str = "Noise" if cl == -1 else f"Cluster {cl}"
        model = t.get("model_name") or "metadata"
        return (f"<b>{a} — {ti}</b><br>"
                f"BPM: {bpm}  Key: {key}  Genre: {g}<br>"
                f"Cluster: {cl_str}  <i>{model}</i>")

    def key_num(k):
        f = _key_to_features(k)
        if not f:
            return 0
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
        "cluster": cluster_labels,
        "bpm": bpms,
        "key_num": [key_num(t.get("key")) for t in tracks],
        "label": [label(t, cl) for t, cl in zip(tracks, cluster_labels)],
        "search": [
            ((t.get("artist") or "") + " " + (t.get("title") or "")).lower()
            for t in tracks
        ],
        # Per-track fields for sidebar
        "artist": [t.get("artist") or "" for t in tracks],
        "title_str": [(t.get("title") or Path(t["path"]).stem) for t in tracks],
        "key_str": [t.get("key") or "" for t in tracks],
        "neighbors": neighbors,
        "neighbor_sims": neighbor_sims,
    }
    return json.dumps(payload)


def _build_cluster_colors_json(unique_labels: list[int]) -> str:
    colors = {}
    ci = 0
    for lbl in sorted(unique_labels):
        if lbl == -1:
            colors["-1"] = "#444444"
        else:
            colors[str(lbl)] = _CLUSTER_PALETTE[ci % len(_CLUSTER_PALETTE)]
            ci += 1
    return json.dumps(colors)


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
    parser.add_argument("--neighbors", type=int, default=5,
                        help="Nearest neighbors to precompute per track for sidebar (default: 5)")
    parser.add_argument("--min-cluster-size", type=int, default=10, dest="min_cluster_size",
                        help="HDBSCAN min_cluster_size (default: 10)")
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
    tracks = _load_tracks(db_path, model_filter=args.model)
    print(f"  {len(tracks)} active tracks loaded", file=sys.stderr)

    embedded = [t for t in tracks if t.get("vector") is not None]
    print(f"  {len(embedded)} tracks have embeddings", file=sys.stderr)

    mode = args.mode
    if mode == "auto":
        mode = "embedding" if len(embedded) >= 30 else "metadata"
    print(f"  Layout mode: {mode}", file=sys.stderr)

    import numpy as np

    if mode == "embedding":
        good, matrix = _build_features_embeddings(tracks)
        metric = "cosine"
        title = f"UMAP on audio embeddings ({len(good)} tracks)"
        subtitle = f"Model: {good[0].get('model_name', '?') if good else '?'} · click a point for next-track suggestions"
    else:
        good, matrix = _build_features_metadata(tracks)
        metric = "euclidean"
        title = f"UMAP on BPM + Key ({len(good)} tracks)"
        subtitle = "Metadata-only view (no embeddings) · click a point for nearest neighbors"

    n = len(good)
    if n < 2:
        print("ERROR: Not enough tracks with the required data to generate a visualization.", file=sys.stderr)
        sys.exit(1)

    print(f"  Running UMAP on {n} points…", file=sys.stderr)
    xs, ys = _umap_2d(matrix, metric=metric)

    # Cluster labels
    print(f"  Running HDBSCAN (min_cluster_size={args.min_cluster_size})…", file=sys.stderr)
    arr = np.array(matrix, dtype=np.float32)
    labels = _cluster_labels(arr, min_cluster_size=args.min_cluster_size)
    n_clusters = len(set(l for l in labels if l >= 0))
    n_noise = labels.count(-1)
    print(f"  Found {n_clusters} cluster(s), {n_noise} noise points", file=sys.stderr)

    # Precompute neighbors
    print(f"  Precomputing top-{args.neighbors} neighbors per track…", file=sys.stderr)
    neighbors = _precompute_neighbors(arr, k=args.neighbors)

    # Compute neighbor cosine similarities for display in sidebar
    arr_norm = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8)
    sims_matrix = arr_norm @ arr_norm.T
    neighbor_sims: list[list[float]] = []
    for i, nb_idxs in enumerate(neighbors):
        neighbor_sims.append([round(float(sims_matrix[i, j]), 4) for j in nb_idxs])

    bpms = [float(t.get("bpm") or 0) for t in good]
    bpm_min = int(min(b for b in bpms if b > 0)) if any(b > 0 for b in bpms) else 60
    bpm_max = int(max(bpms)) if bpms else 200

    unique_labels = sorted(set(labels))
    cluster_colors_json = _build_cluster_colors_json(unique_labels)
    data_json = _build_data_json(good, xs, ys, labels, neighbors, neighbor_sims)

    html = _HTML_TEMPLATE.format(
        title=title,
        subtitle=subtitle,
        data_json=data_json,
        cluster_colors_json=cluster_colors_json,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
    )

    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"\nViz written to: {out.resolve()}", file=sys.stderr)
    print(f"Open in browser: file://{out.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
