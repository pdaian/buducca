from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any


def run_command(command: str | list[str], timeout_seconds: float = 60.0, cwd: str | None = None) -> tuple[int, str, str]:
    args = shlex.split(command) if isinstance(command, str) else command
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        cwd=cwd,
    )
    return completed.returncode, completed.stdout, completed.stderr


def load_json_file(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_json_lines(items: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + ("\n" if items else "")
