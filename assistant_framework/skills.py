from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .module_loader import iter_plugin_modules, load_module_from_file
from .workspace import Workspace


@dataclass
class Skill:
    name: str
    description: str
    run: Callable[[Workspace, dict[str, Any]], str]
    requires_llm_response: bool = False
    args_schema: str = ""


class SkillManager:
    def __init__(self, skills_dir: str | Path) -> None:
        self.skills_dir = Path(skills_dir)

    def _load_module(self, path: Path) -> ModuleType:
        return load_module_from_file(path, kind="skill")

    def _iter_module_files(self) -> list[Path]:
        return iter_plugin_modules(self.skills_dir)

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
        args_schema = self._resolve_args_schema(module, file_path)

        if hasattr(module, "register"):
            registered = module.register()
            return Skill(
                name=registered["name"],
                description=registered.get("description", ""),
                run=registered["run"],
                requires_llm_response=bool(registered.get("requires_llm_response", False)),
                args_schema=str(registered.get("args_schema") or args_schema),
            )

        run = getattr(module, "run", None)
        if run is None or not callable(run):
            raise RuntimeError(f"Skill file {file_path} must expose callable run(workspace, args)")

        name = getattr(module, "NAME", file_path.stem)
        description = getattr(module, "DESCRIPTION", "")
        requires_llm_response = bool(getattr(module, "REQUIRES_LLM_RESPONSE", False))
        return Skill(
            name=name,
            description=description,
            run=run,
            requires_llm_response=requires_llm_response,
            args_schema=args_schema,
        )

    def _resolve_args_schema(self, module: ModuleType, file_path: Path) -> str:
        module_schema = getattr(module, "ARGS_SCHEMA", None)
        if isinstance(module_schema, str) and module_schema.strip():
            return module_schema.strip()

        readme_path = file_path.parent / "README.md"
        if not readme_path.exists():
            return ""

        readme_text = readme_path.read_text(encoding="utf-8")
        match = re.search(
            r"^##\s+Args schema\s*\n```(?:\w+)?\n(.*?)\n```",
            readme_text,
            flags=re.MULTILINE | re.DOTALL,
        )
        if match is None:
            return ""
        return match.group(1).strip()
