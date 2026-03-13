from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .action_runtime import ActionEnvelope
from .module_loader import iter_plugin_modules, load_module_from_file
from .workspace import Workspace


@dataclass
class Skill:
    name: str
    description: str
    run: Callable[[Workspace, dict[str, Any]], str]
    requires_llm_response: bool = False
    args_schema: str = ""
    build_action: Callable[[dict[str, Any]], ActionEnvelope | None] | None = None
    source_path: Path | None = None
    readme_path: Path | None = None


def read_skill_doc_section(readme_path: Path | None, heading: str) -> str:
    if readme_path is None or not readme_path.exists():
        return ""
    readme_text = readme_path.read_text(encoding="utf-8")
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        readme_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return ""
    section = match.group(1).strip()
    lines: list[str] = []
    in_code_block = False
    for raw_line in section.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _split_top_level_schema_fields(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    escape = False
    for char in body:
        if quote is not None:
            current.append(char)
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue
        if char in "{[<(":
            depth += 1
            current.append(char)
            continue
        if char in "}])>":
            depth = max(0, depth - 1)
            current.append(char)
            continue
        if depth == 0 and char in ",;\n":
            field = "".join(current).strip()
            if field:
                parts.append(field)
            current = []
            continue
        current.append(char)
    field = "".join(current).strip()
    if field:
        parts.append(field)
    return parts


def parse_args_schema_fields(args_schema: str) -> list[dict[str, Any]]:
    schema = args_schema.strip()
    if not schema or not (schema.startswith("{") and schema.endswith("}")):
        return []

    body = schema[1:-1].strip()
    if not body:
        return []

    fields: list[dict[str, Any]] = []
    for field_text in _split_top_level_schema_fields(body):
        key, separator, value = field_text.partition(":")
        if not separator:
            return []
        raw_name = key.strip()
        raw_value = value.strip()
        if not raw_name or not raw_value:
            return []
        is_optional = raw_name.endswith("?")
        if is_optional:
            raw_name = raw_name[:-1].strip()
        raw_name = raw_name.strip('\'"')
        if not raw_name:
            return []
        field: dict[str, Any] = {
            "name": raw_name,
            "required": not is_optional,
            "schema": raw_value,
        }
        if raw_value.startswith('"required') or raw_value.startswith("'required"):
            field["required"] = True
        elif raw_value.startswith('"optional') or raw_value.startswith("'optional"):
            field["required"] = False
        fields.append(field)
    return fields


def build_skill_manifest(skill: Skill) -> dict[str, Any]:
    args_schema_fields = parse_args_schema_fields(skill.args_schema)
    manifest: dict[str, Any] = {
        "name": skill.name,
        "description": skill.description,
        "requires_llm_response": skill.requires_llm_response,
        "source_path": str(skill.source_path) if skill.source_path else "",
        "readme_path": str(skill.readme_path) if skill.readme_path else "",
        "prompt_surface": {
            "description": skill.description,
            "args_schema": skill.args_schema,
            "args_schema_fields": args_schema_fields,
        },
        "human_help_surface": {},
    }
    what_it_does = read_skill_doc_section(skill.readme_path, "What it does")
    if what_it_does:
        manifest["human_help_surface"]["what_it_does"] = what_it_does
    return manifest


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
        register = getattr(module, "register", None)
        if register is None or not callable(register):
            raise RuntimeError(f"Skill file {file_path} must expose register()")
        registered = register()
        return Skill(
            name=registered["name"],
            description=registered.get("description", ""),
            run=registered["run"],
            requires_llm_response=bool(registered.get("requires_llm_response", False)),
            args_schema=str(registered.get("args_schema") or args_schema),
            build_action=registered.get("build_action"),
            source_path=file_path,
            readme_path=file_path.parent / "README.md",
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
