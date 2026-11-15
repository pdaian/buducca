"""Small, provider-independent controls for a single skill chain."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class RepeatedResultGuard:
    """Stop only consecutive calls with identical names, arguments, and results."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self._previous: bytes | None = None
        self._count = 0

    def observe(self, name: str, args: dict[str, Any], result: str) -> bool:
        if self.limit is None:
            return False
        fingerprint = hashlib.sha256(
            json.dumps([name, args, result], sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).digest()
        self._count = self._count + 1 if fingerprint == self._previous else 1
        self._previous = fingerprint
        return self._count >= self.limit


def bound_skill_result(result: str, *, step: int, max_chars: int | None) -> str:
    """Keep both ends of large results and explain how to recover the middle."""
    if max_chars is None or len(result) <= max_chars:
        return result
    marker = (
        f"\n[Result from step {step} truncated; {len(result)} characters total. "
        f'Read omitted text with read_skill_result using {{"step": {step}, "offset": 0, "limit": 1000}}. '
        "Offsets are zero-based characters.]\n"
    )
    available = max_chars - len(marker)
    head = (available + 1) // 2
    tail = available // 2
    return result[:head] + marker + (result[-tail:] if tail else "")


def read_skill_result(steps: list[dict[str, Any]], args: dict[str, Any], *, max_chars: int) -> str:
    """Read only completed results in the current turn, never arbitrary traces."""
    step = args.get("step")
    offset = args.get("offset", 0)
    limit = args.get("limit", max_chars)
    for name, value, minimum in (("step", step, 1), ("offset", offset, 0), ("limit", limit, 1)):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            return f"read_skill_result: {name} must be an integer >= {minimum}."
    if step > len(steps):
        return "read_skill_result: step is unavailable in this turn."
    result = steps[step - 1]["skill_result"]
    if offset > len(result):
        return "read_skill_result: offset exceeds the result length."
    # Reserve room for range metadata so every returned page fits the same cap.
    end = min(len(result), offset + limit, offset + max_chars - 160)
    header = f"[Step {step}: characters {offset}:{end} of {len(result)}; next offset {end}]\n"
    return header + result[offset:end]
