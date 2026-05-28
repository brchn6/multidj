from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .constants import CAMELOT_KEY_MAP
from .db import connect, resolve_db_path, table_exists

_ACTIVE = "deleted = 0"
_CAMELOT_RE = re.compile(r"^(?:[1-9]|1[0-2])[AB]$", re.IGNORECASE)


def _normalize_camelot(value: str | None) -> str | None:
  if not value:
    return None
  v = value.strip()
  if not v:
    return None
  mapped = CAMELOT_KEY_MAP.get(v)
  if mapped:
    return mapped
  v = v.upper()
  if _CAMELOT_RE.match(v):
    return v
  return None


def get_camelot_compatibility(key1: str | None, key2: str | None) -> str:
  """Return transition compatibility: compatible | relative | incompatible."""
  k1 = _normalize_camelot(key1)
  k2 = _normalize_camelot(key2)
  if not k1 or not k2:
    return "incompatible"

  n1, l1 = int(k1[:-1]), k1[-1]
  n2, l2 = int(k2[:-1]), k2[-1]

  if n1 == n2 and l1 == l2:
    return "compatible"
  if n1 == n2 and l1 != l2:
    return "relative"
  if l1 == l2 and ((n1 - n2) % 12 in (1, 11)):
    return "compatible"
  return "incompatible"


def _count_active(conn: sqlite3.Connection) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS c FROM tracks WHERE {_ACTIVE}").fetchone()
    return int(row["c"]) if row else 0


def _load_crates_with_tracks(conn: sqlite3.Connection) -> list[dict]:
  crates = conn.execute(
    """
    SELECT id, name, type, show
    FROM crates
    ORDER BY LOWER(name) ASC
    """
  ).fetchall()

  result: list[dict] = []
  for crate in crates:
    tracks_rows = conn.execute(
      f"""
      SELECT
        t.id,
        t.artist,
        t.title,
        t.bpm,
        t."key" AS track_key,
        ct.rowid AS membership_order
      FROM crate_tracks ct
      JOIN tracks t ON t.id = ct.track_id
      WHERE ct.crate_id = ?
        AND {_ACTIVE}
      ORDER BY membership_order ASC, LOWER(COALESCE(t.artist, '')) ASC, LOWER(COALESCE(t.title, '')) ASC
      """,
      (crate["id"],),
    ).fetchall()

    tracks = [
      {
        "id": int(row["id"]),
        "artist": row["artist"] or "",
        "title": row["title"] or "",
        "bpm": float(row["bpm"]) if row["bpm"] is not None else None,
        "key": row["track_key"] or "",
        "membership_order": int(row["membership_order"]),
      }
      for row in tracks_rows
    ]

    transitions = []
    for i in range(len(tracks) - 1):
      status = get_camelot_compatibility(tracks[i].get("key"), tracks[i + 1].get("key"))
      transitions.append({"from_index": i, "to_index": i + 1, "status": status})

    result.append(
      {
        "id": int(crate["id"]),
        "name": crate["name"],
        "type": crate["type"],
        "show": int(crate["show"]),
        "tracks": tracks,
        "transitions": transitions,
      }
    )

  return result


def collect_report_data(conn: sqlite3.Connection) -> dict:
    total_active_tracks = _count_active(conn)

    coverage_rules = {
        "bpm": "bpm IS NOT NULL AND bpm > 0",
        "genre": "genre IS NOT NULL AND TRIM(genre) <> ''",
        "key": "\"key\" IS NOT NULL AND TRIM(\"key\") <> ''",
        "rating": "rating IS NOT NULL AND rating > 0",
        "energy": "energy IS NOT NULL AND energy > 0",
    }

    metadata_coverage: dict[str, dict[str, int | float]] = {}
    for field, rule in coverage_rules.items():
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM tracks WHERE {_ACTIVE} AND {rule}"
        ).fetchone()
        present = int(row["c"]) if row else 0
        missing = max(total_active_tracks - present, 0)
        pct = (present / total_active_tracks * 100.0) if total_active_tracks else 0.0
        metadata_coverage[field] = {
            "present": present,
            "missing": missing,
            "coverage_pct": round(pct, 2),
        }

    top_genres_rows = conn.execute(
        f"""
        SELECT genre, COUNT(*) AS count
        FROM tracks
        WHERE {_ACTIVE}
          AND genre IS NOT NULL
          AND TRIM(genre) <> ''
        GROUP BY genre
        ORDER BY count DESC, LOWER(genre) ASC
        LIMIT 20
        """
    ).fetchall()

    top_genres = [
        {"genre": str(r["genre"]), "count": int(r["count"])}
        for r in top_genres_rows
    ]

    crates = _load_crates_with_tracks(conn)

    return {
        "total_active_tracks": total_active_tracks,
        "metadata_coverage": metadata_coverage,
        "missing_counts": {
            field: int(values["missing"])
            for field, values in metadata_coverage.items()
        },
        "top_genres": top_genres,
        "crates": crates,
    }


def render_dashboard_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>MultiDJ Interactive Dashboard</title>
  <style>
    :root {{
      --bg: #0a0a0a;
      --surface: #121212;
      --surface-2: #181818;
      --ink: #f2f5f7;
      --muted: #9ba7b3;
      --line: #2a2d31;
      --accent: #5fd4cc;
      --warn: #f5c96a;
      --bad: #ff7d7d;
      --ok: #71df90;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
      background: radial-gradient(circle at top right, #171717, var(--bg) 40%);
      color: var(--ink);
    }}
    .wrap {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px;
    }}
    .header {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px 22px;
      margin-bottom: 16px;
    }}
    .header h1 {{ margin: 0 0 8px 0; font-size: 26px; letter-spacing: .02em; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
    }}
    .label {{ font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }}
    .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; color: var(--accent); }}
    .grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }}
    @media (max-width: 980px) {{ .grid {{ grid-template-columns: 1fr; }} }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
    }}
    .bar-row {{ display: grid; grid-template-columns: 180px 1fr 56px; gap: 8px; align-items: center; margin: 6px 0; }}
    .bar-wrap {{ height: 12px; background: #1f2328; border-radius: 8px; overflow: hidden; }}
    .bar {{ height: 100%; background: linear-gradient(90deg, #4371da, var(--accent)); }}
    .clickable {{ cursor: pointer; }}
    .clickable:hover {{ opacity: .84; }}
    .section-title {{ margin: 0 0 8px 2px; font-size: 18px; }}
    .small {{ color: var(--muted); font-size: 12px; }}
    .crate-list {{ display: flex; flex-direction: column; gap: 8px; max-height: 360px; overflow: auto; }}
    .crate-item {{ padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-2); }}
    .crate-item.active {{ border-color: var(--accent); }}
    .track-row {{ border-bottom: 1px solid var(--line); padding: 8px 0; }}
    .track-row:last-child {{ border-bottom: none; }}
    button {{
      background: #222;
      color: var(--ink);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 6px 10px;
      cursor: pointer;
    }}
    button:hover {{ border-color: var(--accent); }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; border: 1px solid var(--line); }}
    .ok {{ color: var(--ok); border-color: #2a5e39; }}
    .warn {{ color: var(--warn); border-color: #5d5130; }}
    .bad {{ color: var(--bad); border-color: #6d3a3a; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: transparent;
      border: 1px solid var(--line);
      border-radius: 12px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 14px;
    }}
    th {{ background: #1a2128; }}
    tr:last-child td {{ border-bottom: 0; }}
  </style>
</head>
<body>
  <div id="root"></div>
  <script>
    window.__DATA__ = {payload};
  </script>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script>
    (() => {{
      const e = React.createElement;

      function parseCamelot(key) {{
        if (!key) return null;
        const k = String(key).trim().toUpperCase();
        const m = k.match(/^(?:([1-9])|(1[0-2]))([AB])$/);
        if (!m) return null;
        return {{ n: Number(m[1] || m[2]), l: m[3] }};
      }}

      function compatibility(k1, k2) {{
        const a = parseCamelot(k1);
        const b = parseCamelot(k2);
        if (!a || !b) return "incompatible";
        if (a.n === b.n && a.l === b.l) return "compatible";
        if (a.n === b.n && a.l !== b.l) return "relative";
        const diff = (a.n - b.n + 12) % 12;
        if (a.l === b.l && (diff === 1 || diff === 11)) return "compatible";
        return "incompatible";
      }}

      function transitionBadge(status) {{
        if (status === "compatible") return e("span", {{ className: "badge ok" }}, "✅ compatible");
        if (status === "relative") return e("span", {{ className: "badge warn" }}, "⚠️ risky");
        return e("span", {{ className: "badge bad" }}, "❌ incompatible");
      }}

      function App() {{
        const data = window.__DATA__ || {{}};
        const crates = data.crates || [];
        const [genreFilter, setGenreFilter] = React.useState(null);
        const [selectedCrateId, setSelectedCrateId] = React.useState(crates.length ? crates[0].id : null);
        const [trackOverrides, setTrackOverrides] = React.useState({{}});
        const [preferred, setPreferred] = React.useState({{}});
        const [flagged, setFlagged] = React.useState({{}});

        const selectedCrate = crates.find(c => c.id === selectedCrateId) || null;
        const tracks = React.useMemo(() => {{
          if (!selectedCrate) return [];
          return trackOverrides[selectedCrate.id] || selectedCrate.tracks || [];
        }}, [selectedCrate, trackOverrides]);

        const filteredCrates = React.useMemo(() => {{
          if (!genreFilter) return crates;
          return crates.filter(c => (c.tracks || []).some(t => String(t.title || "").toLowerCase().includes(genreFilter.toLowerCase()) || String(t.artist || "").toLowerCase().includes(genreFilter.toLowerCase())));
        }}, [crates, genreFilter]);

        const moveTrack = (idx, dir) => {{
          if (!selectedCrate) return;
          const arr = [...tracks];
          const j = idx + dir;
          if (j < 0 || j >= arr.length) return;
          const tmp = arr[idx]; arr[idx] = arr[j]; arr[j] = tmp;
          setTrackOverrides(prev => ({{ ...prev, [selectedCrate.id]: arr }}));
        }};

        const transitions = React.useMemo(() => {{
          const out = [];
          for (let i = 0; i < tracks.length - 1; i++) {{
            out.push({{ idx: i, status: compatibility(tracks[i].key, tracks[i+1].key) }});
          }}
          return out;
        }}, [tracks]);

        const active = data.total_active_tracks || 0;
        const cov = data.metadata_coverage || {{}};
        const topGenres = data.top_genres || [];
        const missing = data.missing_counts || {{}};

        return e("div", {{ className: "wrap" }},
          e("section", {{ className: "header" }},
            e("h1", null, "MultiDJ Interactive Dashboard"),
            e("div", {{ className: "meta" }}, `DB Path: ${{data.db_path || ""}}`),
            e("div", {{ className: "meta" }}, `Generated: ${{data.generated_at || ""}}`)
          ),

          e("section", {{ className: "cards" }},
            e("article", {{ className: "card" }}, e("div", {{ className: "label" }}, "Total Tracks"), e("div", {{ className: "value" }}, active)),
            ...["bpm", "genre", "key", "energy"].map(k => e("article", {{ className: "card", key: k }},
              e("div", {{ className: "label" }}, `${{k.toUpperCase()}} Coverage`),
              e("div", {{ className: "value" }}, `${{(cov[k]?.coverage_pct ?? 0).toFixed(1)}}%`)
            ))
          ),

          e("section", {{ className: "grid" }},
            e("div", {{ className: "panel" }},
              e("h2", {{ className: "section-title" }}, "Genre Distribution"),
              e("div", {{ className: "small" }}, "Click a genre bar to filter crate list context"),
              ...topGenres.map(g => e("div", {{ className: "bar-row clickable", key: g.genre, onClick: () => setGenreFilter(g.genre === genreFilter ? null : g.genre) }},
                e("div", null, g.genre),
                e("div", {{ className: "bar-wrap" }}, e("div", {{ className: "bar", style: {{ width: `${{Math.max(4, (g.count / Math.max(1, topGenres[0]?.count || 1)) * 100)}}%` }} }})),
                e("div", null, g.count)
              ))
            ),

            e("div", {{ className: "panel" }},
              e("h2", {{ className: "section-title" }}, "Missing Metadata"),
              e("table", null,
                e("thead", null, e("tr", null, e("th", null, "Field"), e("th", null, "Missing"))),
                e("tbody", null,
                  ...Object.keys(missing).map(k => e("tr", {{ key: k }}, e("td", null, k.toUpperCase()), e("td", null, missing[k])))
                )
              )
            ),

            e("div", {{ className: "panel" }},
              e("h2", {{ className: "section-title" }}, "Crates"),
              genreFilter ? e("div", {{ className: "small" }}, `Filtered by: ${{genreFilter}}`) : null,
              e("div", {{ className: "crate-list" }},
                ...filteredCrates.map(c => e("div", {{
                  key: c.id,
                  className: `crate-item clickable ${{c.id === selectedCrateId ? "active" : ""}}`,
                  onClick: () => setSelectedCrateId(c.id)
                }}, `${{c.name}} (${{(c.tracks || []).length}})`))
              )
            ),

            e("div", {{ className: "panel" }},
              e("h2", {{ className: "section-title" }}, "Crate Tracks + Harmonic Validation"),
              !selectedCrate ? e("div", {{ className: "small" }}, "Select a crate") : e(React.Fragment, null,
                e("div", {{ className: "small" }}, `Crate: ${{selectedCrate.name}}`),
                ...tracks.map((t, idx) => e("div", {{ className: "track-row", key: `${{t.id}}-${{idx}}` }},
                  e("div", null, `${{idx + 1}}. ${{t.artist || ""}} — ${{t.title || ""}}`),
                  e("div", {{ className: "small" }}, `BPM: ${{t.bpm ?? "-"}} | Key: ${{t.key || "-"}}`),
                  e("div", {{ style: {{ display: "flex", gap: "8px", marginTop: "6px" }} }},
                    e("button", {{ onClick: () => moveTrack(idx, -1) }}, "↑"),
                    e("button", {{ onClick: () => moveTrack(idx, 1) }}, "↓"),
                    e("button", {{ onClick: () => setPreferred(p => ({{ ...p, [`${{selectedCrate.id}}:${{idx}}`]: !p[`${{selectedCrate.id}}:${{idx}}`] }})) }},
                      preferred[`${{selectedCrate.id}}:${{idx}}`] ? "★ preferred" : "☆ mark preferred"),
                    e("button", {{ onClick: () => setFlagged(f => ({{ ...f, [`${{selectedCrate.id}}:${{idx}}`]: !f[`${{selectedCrate.id}}:${{idx}}`] }})) }},
                      flagged[`${{selectedCrate.id}}:${{idx}}`] ? "🚩 flagged" : "⚑ flag")
                  ),
                  idx < transitions.length ? e("div", {{ style: {{ marginTop: "6px" }} }}, transitionBadge(transitions[idx].status)) : null
                ))
              )
            )
          )
        );
      }}

      ReactDOM.createRoot(document.getElementById("root")).render(e(App));
    }})();
  </script>
</body>
</html>
"""


def collect_report_data_from_db(db_path: str | None) -> dict:
    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        if not table_exists(conn, "tracks"):
            raise RuntimeError("MultiDJ DB is empty. Run 'multidj import mixxx' first.")
        data = collect_report_data(conn)

    data["db_path"] = str(resolve_db_path(db_path))
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


def write_dashboard_report(db_path: str | None, output_path: str) -> None:
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    data = collect_report_data_from_db(db_path)
    html_doc = render_dashboard_html(data)
    output.write_text(html_doc, encoding="utf-8")


def write_html_report(db_path: str | None, output_path: str) -> None:
    # Backward-compatible alias: pipeline and tests may still import this name.
    write_dashboard_report(db_path=db_path, output_path=output_path)
