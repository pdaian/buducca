from __future__ import annotations

from pathlib import Path
import shutil


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._resolved_root = self.root.resolve()

    def resolve(self, relative_path: str) -> Path:
        target = (self._resolved_root / relative_path).resolve()
        try:
            target.relative_to(self._resolved_root)
        except ValueError as exc:
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

    def create_dir(self, relative_path: str) -> None:
        dir_path = self.resolve(relative_path)
        dir_path.mkdir(parents=True, exist_ok=True)

    def delete_dir(self, relative_path: str) -> None:
        dir_path = self.resolve(relative_path)
        if not dir_path.exists():
            return
        if not dir_path.is_dir():
            raise ValueError(f"Not a directory: {relative_path}")
        shutil.rmtree(dir_path)

    def move_file_to_dir(self, relative_path: str, destination_dir: str) -> str:
        source = self.resolve(relative_path)
        if not source.exists():
            raise ValueError(f"File not found: {relative_path}")
        if not source.is_file():
            raise ValueError(f"Not a file: {relative_path}")

        target_dir = self.resolve(destination_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        destination = target_dir / source.name
        source.rename(destination)
        return str(destination.relative_to(self.root))
