from __future__ import annotations

import datetime as dt
import json
import shlex
from pathlib import Path
from difflib import SequenceMatcher

from assistant_framework.collector_shell import run_command
from assistant_framework.workspace import Workspace

NAME = "llm_based"
INTERVAL_SECONDS = 24 * 60 * 60


def _default_prompt_template() -> str:
    return (
        "Current date/time (UTC, accurate to the minute): {now}\n\n"
        "You are running a memory compressor for {file_path}. "
        "Remove redundant information while preserving all important, non-overlapping details. "
        'Return JSON with keys "compressed_content" and "removed_content".'
    )


def _extract_removed_content(original: str, compressed: str) -> str:
    matcher = SequenceMatcher(a=original, b=compressed)
    chunks = [original[i1:i2] for tag, i1, i2, _, _ in matcher.get_opcodes() if tag in {"delete", "replace"} and original[i1:i2]]
    return "".join(chunks)


def _parse_llm_output(output: str, original: str) -> tuple[str, str]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        compressed = output
        return compressed, _extract_removed_content(original, compressed)
    if not isinstance(parsed, dict):
        compressed = output
        return compressed, _extract_removed_content(original, compressed)
    compressed = str(parsed.get("compressed_content", ""))
    removed = str(parsed.get("removed_content", ""))
    if not removed:
        removed = _extract_removed_content(original, compressed)
    return compressed, removed


def create_compressor(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    command = str(config.get("command", "python3 scripts/memory_compressor.py"))
    timeout = float(config.get("timeout_seconds", 90))
    files = list(
        config.get(
            "files",
            [
                {
                    "path": "learnings",
                    "interval_seconds": INTERVAL_SECONDS,
                    "backup_path": "learnings.back",
                    "prompt": _default_prompt_template(),
                }
            ],
        )
    )

    state: dict[str, float] = {}

    def _run(workspace: Workspace) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        now_iso = now.strftime("%Y-%m-%d %H:%M UTC")
        today = now.date().isoformat()

        for file_cfg in files:
            path = str(file_cfg.get("path", "")).strip()
            if not path:
                continue
            per_file_interval = float(file_cfg.get("interval_seconds", interval))
            key = path
            if now.timestamp() < state.get(key, 0.0):
                continue

            current_content = workspace.read_text(path, default="")
            prompt = str(file_cfg.get("prompt", _default_prompt_template())).format(file_path=path, now=now_iso)
            payload = {
                "file_path": path,
                "current_date_time": now_iso,
                "prompt": prompt,
                "content": current_content,
                "response_format": {
                    "type": "json",
                    "schema": {
                        "compressed_content": "string",
                        "removed_content": "string",
                    },
                },
            }
            code, stdout, stderr = run_command([*shlex.split(command), json.dumps(payload)], timeout_seconds=timeout)
            if code != 0:
                raise RuntimeError(f"llm compression failed for {path}: {stderr.strip()}")
            compressed, removed = _parse_llm_output(stdout, current_content)
            if not compressed.endswith("\n"):
                compressed += "\n"
            workspace.archive_text(path, removed, reason="llm_based")

            backup_path = str(file_cfg.get("backup_path", f"{path}.back")).strip()
            backup_stamp_path = str(file_cfg.get("backup_stamp_path", f"{backup_path}.date")).strip()
            latest_backup_day = workspace.read_text(backup_stamp_path, default="").strip()
            if backup_path and latest_backup_day != today:
                workspace.write_text(backup_path, current_content)
                workspace.write_text(backup_stamp_path, today)

            workspace.write_text(path, compressed)
            state[key] = now.timestamp() + per_file_interval

    return {"name": NAME, "interval_seconds": interval, "run": _run}
