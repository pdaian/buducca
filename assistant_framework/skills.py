from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .workspace import Workspace


@dataclass
class Skill:
    name: str
    description: str
    run: Callable[[Workspace, dict[str, Any]], str]
    requires_llm_response: bool = False


class SkillManager:
    def __init__(self, skills_dir: str | Path) -> None:
        self.skills_dir = Path(skills_dir)

    def _load_module(self, path: Path) -> ModuleType:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load skill module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _iter_module_files(self) -> list[Path]:
        modules: list[Path] = []
        for file_path in sorted(self.skills_dir.glob("*.py")):
            if file_path.name.startswith("_"):
                continue
            modules.append(file_path)

        for subdir in sorted(path for path in self.skills_dir.iterdir() if path.is_dir()):
            if subdir.name.startswith("_"):
                continue
            init_file = subdir / "__init__.py"
            if init_file.exists():
                modules.append(init_file)
        return modules

    def load(self) -> dict[str, Skill]:
        skills: dict[str, Skill] = {}
        if not self.skills_dir.exists():
            return skills

        for file_path in self._iter_module_files():
            module = self._load_module(file_path)
            skill = self._build_skill(module, file_path)
            skills[skill.name] = skill
        return skills

    def _build_skill(self, module: ModuleType, file_path: Path) -> Skill:
        if hasattr(module, "register"):
            registered = module.register()
            return Skill(
                name=registered["name"],
                description=registered.get("description", ""),
                run=registered["run"],
                requires_llm_response=bool(registered.get("requires_llm_response", False)),
            )

        run = getattr(module, "run", None)
        if run is None or not callable(run):
            raise RuntimeError(f"Skill file {file_path} must expose callable run(workspace, args)")

        name = getattr(module, "NAME", file_path.stem)
        description = getattr(module, "DESCRIPTION", "")
        requires_llm_response = bool(getattr(module, "REQUIRES_LLM_RESPONSE", False))
        return Skill(name=name, description=description, run=run, requires_llm_response=requires_llm_response)
