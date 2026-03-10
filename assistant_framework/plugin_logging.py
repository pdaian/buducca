from __future__ import annotations

import logging
from typing import Any


def log_plugin_event(kind: str, name: str, event: str, **fields: Any) -> None:
    parts = [f"{kind}={name}", f"event={event}"]
    for key in sorted(fields):
        value = fields[key]
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logging.info(" ".join(parts))
