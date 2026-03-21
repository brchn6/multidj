from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class LibrarySummary:
    total_tracks: int
    total_crates: int
    tracks_with_genre: int
    tracks_with_bpm: int
    tracks_with_key: int
    tracks_with_rating: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
