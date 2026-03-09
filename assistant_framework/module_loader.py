from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_module_from_file(path: Path, *, kind: str) -> ModuleType:
    """Load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {kind} module from {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def iter_plugin_modules(root_dir: Path) -> list[Path]:
    """Return discoverable plugin modules from a directory.

    Supports both flat modules (`plugin.py`) and package plugins
    (`plugin/__init__.py`). Hidden and private entries are ignored.
    """
    modules: list[Path] = []

    for file_path in sorted(root_dir.glob("*.py")):
        if file_path.name.startswith("_"):
            continue
        modules.append(file_path)

    for subdir in sorted(path for path in root_dir.iterdir() if path.is_dir()):
        if subdir.name.startswith("_"):
            continue
        init_file = subdir / "__init__.py"
        if init_file.exists():
            modules.append(init_file)

    return modules
