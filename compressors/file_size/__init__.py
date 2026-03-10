from __future__ import annotations

from pathlib import Path

from assistant_framework.workspace import Workspace

NAME = "file_size"
INTERVAL_SECONDS = 3600


def _iter_target_files(root: Path, include_patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in include_patterns:
        files.extend(path for path in root.glob(pattern) if path.is_file())
    return sorted(set(files))


def create_compressor(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    include_patterns = list(config.get("include", ["*"]))
    max_lines = int(config.get("max_lines", 200))

    def _run(workspace: Workspace) -> None:
        for file_path in _iter_target_files(workspace.root, include_patterns):
            rel = file_path.relative_to(workspace.root)
            lines = workspace.read_text(rel.as_posix(), default="").splitlines()
            if len(lines) <= max_lines:
                continue
            truncated = "\n".join(lines[-max_lines:]) + "\n"
            workspace.write_text(rel.as_posix(), truncated)

    return {"name": NAME, "interval_seconds": interval, "run": _run}
