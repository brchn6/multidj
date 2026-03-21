from __future__ import annotations

import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from .db import resolve_db_path, ensure_db_exists


DEFAULT_BACKUP_DIR = Path("~/.mixxx/backups").expanduser()


@dataclass
class BackupResult:
    source: str
    backup_path: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def create_backup(db_path: str | None = None, backup_dir: str | None = None) -> BackupResult:
    source = resolve_db_path(db_path)
    ensure_db_exists(source)

    target_dir = Path(backup_dir).expanduser() if backup_dir else DEFAULT_BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = target_dir / f"{source.name}.{ts}.backup"
    shutil.copy2(source, backup_path)

    return BackupResult(
        source=str(source),
        backup_path=str(backup_path),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
