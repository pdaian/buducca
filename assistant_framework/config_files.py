from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_file(path: str | Path) -> Any:
    file_path = Path(path)
    try:
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {file_path}") from exc
    except json.JSONDecodeError as exc:
        line = exc.doc.splitlines()[exc.lineno - 1] if exc.doc else ""
        pointer = " " * max(exc.colno - 1, 0) + "^"
        message = (
            f"Invalid JSON in config file {file_path} at line {exc.lineno}, column {exc.colno}: {exc.msg}\n"
            f"{line}\n"
            f"{pointer}"
        )
        raise ValueError(message) from exc


def _merge_values(base: Any, incoming: Any) -> Any:
    if isinstance(base, dict) and isinstance(incoming, dict):
        merged = dict(base)
        for key, value in incoming.items():
            if key in merged:
                merged[key] = _merge_values(merged[key], value)
            else:
                merged[key] = value
        return merged
    return incoming


def _nested_payload(relative_path: Path, payload: Any) -> Any:
    parts = list(relative_path.parts)
    if not parts:
        return payload

    leaf = Path(parts[-1]).stem
    key_parts = parts[:-1]
    if leaf != "index":
        key_parts.append(leaf)

    wrapped = payload
    for key in reversed(key_parts):
        wrapped = {key: wrapped}
    return wrapped


def load_json_path(path: str | Path) -> Any:
    config_path = Path(path)
    if config_path.is_dir():
        merged: Any = {}
        for child in sorted(config_path.rglob("*.json")):
            if child.name.startswith("_"):
                continue
            payload = read_json_file(child)
            merged = _merge_values(merged, _nested_payload(child.relative_to(config_path), payload))
        return merged
    return read_json_file(config_path)


def load_named_config_map(path: str | Path, *, section_name: str | None = None) -> dict[str, Any]:
    raw = load_json_path(path)
    if not isinstance(raw, dict):
        return {}
    if section_name and section_name in raw:
        section = raw.get(section_name)
        return section if isinstance(section, dict) else {}
    return raw
