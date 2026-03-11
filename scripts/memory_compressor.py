from __future__ import annotations

import json
import sys


def _dedupe_lines(text: str) -> str:
    seen: set[str] = set()
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(raw)
    return "\n".join(kept)


def main() -> int:
    if len(sys.argv) < 2:
        print("", end="")
        return 0

    payload = json.loads(sys.argv[1])
    content = str(payload.get("content", ""))
    prompt = str(payload.get("prompt", "")).strip()
    now = str(payload.get("current_date_time", "")).strip()

    # Lightweight default compressor implementation; replace with your LLM call if desired.
    header = f"# Memory compressed at {now}\n" if now else ""
    prompt_note = f"# Prompt: {prompt}\n" if prompt else ""
    compressed = _dedupe_lines(content)
    removed = ""
    compressed_lines = {line.strip().lower() for line in compressed.splitlines() if line.strip()}
    removed_lines: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower() in compressed_lines:
            compressed_lines.remove(line.lower())
            continue
        removed_lines.append(raw)
    if removed_lines:
        removed = "\n".join(removed_lines) + "\n"
    sys.stdout.write(
        json.dumps(
            {
                "compressed_content": f"{header}{prompt_note}{compressed}".strip() + "\n",
                "removed_content": removed,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
