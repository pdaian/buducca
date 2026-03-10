from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _module_name_for_path(path: Path, *, kind: str) -> str:
    resolved = path.resolve()
    sanitized_parts = [
        "".join(ch if ch.isalnum() else "_" for ch in part)
        for part in resolved.parts
    ]
    if path.name == "__init__.py":
        package_name = sanitized_parts[-2] if len(sanitized_parts) >= 2 else path.parent.name
        return f"_codex_{kind}_{package_name}_{abs(hash(str(resolved.parent)))}"
    stem = sanitized_parts[-1].rsplit(".", 1)[0]
    return f"_codex_{kind}_{stem}_{abs(hash(str(resolved)))}"


def load_module_from_file(path: Path, *, kind: str) -> ModuleType:
    """Load a Python module from a file path."""
    module_name = _module_name_for_path(path, kind=kind)
    kwargs = {"submodule_search_locations": [str(path.parent)]} if path.name == "__init__.py" else {}
    spec = importlib.util.spec_from_file_location(module_name, path, **kwargs)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {kind} module from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
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
