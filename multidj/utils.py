from __future__ import annotations

import json
from typing import Any


def emit(data: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if isinstance(data, dict):
        for key, value in data.items():
            print(f"{key}: {value}")
        return

    if isinstance(data, list):
        for item in data:
            print(item)
        return

    print(data)
