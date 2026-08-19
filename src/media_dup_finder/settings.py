"""Conservative JSON settings persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import user_data_dir


DEFAULT_SETTINGS: Dict[str, Any] = {
    "recursive": True,
    "similarity_mode": "严格作品识别（推荐）",
    "read_metadata": True,
    "hash_mode": "smart",
    "window_geometry": "1180x760",
    "last_roots": [],
}


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    result = dict(DEFAULT_SETTINGS)
    target = path or settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in DEFAULT_SETTINGS:
                if key in payload:
                    result[key] = payload[key]
            # Migrate the v1.1 checkbox without making users configure it again.
            if "hash_mode" not in payload and "detect_hash" in payload:
                result["hash_mode"] = "smart" if bool(payload["detect_hash"]) else "off"
    except (OSError, ValueError, TypeError):
        pass
    return result


def save_settings(settings: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = {key: settings.get(key, default) for key, default in DEFAULT_SETTINGS.items()}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
