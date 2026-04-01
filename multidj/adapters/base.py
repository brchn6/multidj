from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path


class SyncAdapter(ABC):
    """Base class for DJ software sync adapters."""

    @abstractmethod
    def import_all(self, multidj_db_path: Path, apply: bool = False) -> dict:
        """Import all tracks from the source into MultiDJ DB.

        Returns summary dict with mode, total_imported, already_present, updated, errors.
        If apply=False: dry-run, returns what would be imported without writing.
        """

    @abstractmethod
    def push_track(self, track: dict, multidj_db_path: Path) -> bool:
        """Push a single track's metadata from MultiDJ to the target.

        Returns True on success, False on failure.
        """

    @abstractmethod
    def full_sync(self, multidj_db_path: Path, apply: bool = False) -> dict:
        """Push all dirty tracks to the target.

        Returns summary dict with mode, total_pushed, errors.
        """
