from __future__ import annotations

import sys
from typing import Any

import numpy as np

from .db import connect, ensure_not_empty
from .embed import load_embeddings_from_db

# Optional dependency — imported at module level so tests can patch multidj.cluster.OpenAI.
# Raises RuntimeError with install instructions if openai is missing and name_cluster() is called.
try:
    from openai import OpenAI  # type: ignore
except ImportError:
    OpenAI = None  # type: ignore


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _numbered_name(idx: int) -> str:
    return f"Cluster-{idx:02d}"


def cluster_embeddings(vectors: np.ndarray, min_cluster_size: int) -> np.ndarray:
    """UMAP 512d→10d then HDBSCAN. Returns integer label array (-1 = noise).

    Falls back to PCA for small datasets (< 30 points) where UMAP's graph
    construction is unstable, especially with near-duplicate vectors.
    """
    try:
        import umap  # type: ignore
        import hdbscan  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Missing optional dependency 'embeddings'. Install with:\n\n"
            "    uv sync --extra embeddings\n"
        )

    n = len(vectors)
    n_components = min(10, max(2, n - 2))

    if n < 30:
        # Too few points for stable UMAP graph construction; PCA handles
        # duplicate vectors gracefully and is deterministic.
        from sklearn.decomposition import PCA
        reduced = PCA(n_components=n_components, random_state=42).fit_transform(vectors)
    else:
        reducer = umap.UMAP(
            n_components=n_components,
            n_neighbors=min(15, n - 1),
            min_dist=0.1,
            metric="cosine",
            random_state=42,
        )
        reduced = reducer.fit_transform(vectors)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    return clusterer.fit_predict(reduced)


def name_cluster(track_samples: list[dict[str, Any]], llm_config: dict[str, Any]) -> str:
    """Call LLM to generate a 2–3 word evocative crate name for a cluster."""
    if OpenAI is None:
        raise RuntimeError(
            "Missing optional dependency 'embeddings'. Install with:\n\n"
            "    uv sync --extra embeddings\n"
        )
    client = OpenAI(base_url=llm_config["base_url"], api_key=llm_config["api_key"])
    model = llm_config.get("model", "gpt-3.5-turbo")

    track_lines = "\n".join(
        f'- "{t.get("artist") or "Unknown"} — {t.get("title") or "Unknown"}"'
        f' (genre: {t.get("genre") or "?"}, BPM: {t.get("bpm") or "?"}, key: {t.get("key") or "?"})'
        for t in track_samples[:20]
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": (
                "You are naming DJ crates. Give a short evocative 2–3 word name for this group of tracks.\n"
                "Use DJ-friendly language. No quotes, no punctuation, no explanation.\n\n"
                f"Tracks:\n{track_lines}\n\n"
                "Crate name (2–3 words only):"
            ),
        }],
        max_tokens=20,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def _write_vibe_crates(
    conn,
    clusters: dict[int, list[int]],
    names: dict[int, str],
    prefix: str,
) -> list[dict[str, Any]]:
    """Clear all existing Vibe/ crates and rebuild from cluster assignments."""
    old_ids = conn.execute(
        "SELECT id FROM crates WHERE name LIKE ?", (f"{prefix}%",)
    ).fetchall()
    for row in old_ids:
        conn.execute("DELETE FROM crate_tracks WHERE crate_id = ?", (row["id"],))
    conn.execute("DELETE FROM crates WHERE name LIKE ?", (f"{prefix}%",))

    written: list[dict[str, Any]] = []
    for label, track_ids in clusters.items():
        crate_name = (
            f"{prefix}Unclassified" if label == -1
            else f"{prefix}{names.get(label, _numbered_name(label))}"
        )
        conn.execute(
            "INSERT OR REPLACE INTO crates (name, type, show) VALUES (?, 'auto', 1)",
            (crate_name,),
        )
        crate_id = conn.execute(
            "SELECT id FROM crates WHERE name = ?", (crate_name,)
        ).fetchone()["id"]
        conn.executemany(
            "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
            [(crate_id, tid) for tid in track_ids],
        )
        written.append({"name": crate_name, "track_count": len(track_ids)})

    conn.commit()
    return written


def cluster_vibe(
    db_path: str | None = None,
    apply: bool = False,
    min_cluster_size: int = 5,
    prefix: str = "Vibe/",
    llm_config: dict[str, Any] | None = None,
    backup_dir: str | None | bool = None,
) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)
        track_ids, vectors = load_embeddings_from_db(conn)

    total_embedded = len(track_ids)
    if total_embedded < min_cluster_size * 2:
        raise RuntimeError(
            f"Too few embedded tracks ({total_embedded}). "
            f"Need at least {min_cluster_size * 2}. "
            f"Run 'multidj analyze embed --apply' first."
        )

    _log(f"cluster vibe — clustering {total_embedded} embeddings (min_cluster_size={min_cluster_size})")
    labels = cluster_embeddings(vectors, min_cluster_size)

    clusters: dict[int, list[int]] = {}
    for tid, label in zip(track_ids, labels.tolist()):
        clusters.setdefault(int(label), []).append(tid)

    n_clusters = len(set(labels) - {-1})
    noise_count = len(clusters.get(-1, []))
    _log(f"  found {n_clusters} clusters, {noise_count} noise tracks")

    names: dict[int, str] = {}
    with connect(db_path, readonly=True) as conn:
        for label, tids in clusters.items():
            if label == -1:
                continue
            sample_rows = conn.execute(
                "SELECT artist, title, genre, bpm, key FROM tracks"
                " WHERE id IN ({}) ORDER BY play_count DESC LIMIT 20".format(
                    ",".join("?" * min(20, len(tids)))
                ),
                tids[:20],
            ).fetchall()
            sample = [dict(r) for r in sample_rows]

            if llm_config:
                try:
                    names[label] = name_cluster(sample, llm_config)
                except Exception as exc:
                    _log(f"  LLM naming failed for cluster {label}: {exc} — using numbered name")
                    names[label] = _numbered_name(label)
            else:
                names[label] = _numbered_name(label)
            _log(f"  cluster {label}: {len(tids)} tracks → '{prefix}{names[label]}'")

    crate_list = [
        {"name": f"{prefix}{names.get(lbl, _numbered_name(lbl))}", "track_count": len(tids)}
        for lbl, tids in clusters.items() if lbl != -1
    ]
    if -1 in clusters:
        crate_list.append({"name": f"{prefix}Unclassified", "track_count": noise_count})

    if not apply:
        return {
            "mode": "dry_run",
            "total_embedded": total_embedded,
            "clusters_found": n_clusters,
            "noise_tracks": noise_count,
            "crates_written": 0,
            "clusters": crate_list,
        }

    with connect(db_path, readonly=False) as conn:
        written = _write_vibe_crates(conn, clusters, names, prefix)

    return {
        "mode": "apply",
        "total_embedded": total_embedded,
        "clusters_found": n_clusters,
        "noise_tracks": noise_count,
        "crates_written": len(written),
        "clusters": written,
    }
