from __future__ import annotations

from pathlib import Path


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError(f"Path escapes workspace: {relative_path}")
        return target

    def read_text(self, relative_path: str, default: str = "") -> str:
        file_path = self.resolve(relative_path)
        if not file_path.exists():
            return default
        return file_path.read_text(encoding="utf-8")

    def write_text(self, relative_path: str, content: str) -> None:
        file_path = self.resolve(relative_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    def append_text(self, relative_path: str, content: str) -> None:
        file_path = self.resolve(relative_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("a", encoding="utf-8") as f:
            f.write(content)
