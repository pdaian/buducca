from __future__ import annotations

import re
from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.workspace import Workspace

NAME = "search_files"
DESCRIPTION = (
    "Search workspace text files with literal or regex matching across a single file, "
    "a list of files, or a directory tree. "
    "Args: pattern (required), path or paths (optional file or directory scope), "
    "regex (optional bool), case_sensitive (optional bool), max_matches (optional int, default 50)."
)
ARGS_SCHEMA = (
    '{"pattern":"required","path":"optional/file/or/dir","paths":["optional/file/or/dir"],'
    '"regex":false,"case_sensitive":false,"max_matches":50}'
)

_DEFAULT_MAX_MATCHES = 50


def _normalize_workspace_path(path: str) -> str:
    normalized = path.strip()
    while "workspace/" in normalized:
        normalized = normalized.replace("workspace/", "")
    return normalized


def _resolve_paths(args: dict[str, Any]) -> list[str] | None:
    raw_paths = args.get("paths")
    if raw_paths is None:
        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            return None
        raw_paths = [raw_path]
    if isinstance(raw_paths, list):
        return [_normalize_workspace_path(str(item).strip()) for item in raw_paths if str(item).strip()]
    value = _normalize_workspace_path(str(raw_paths).strip())
    return [value] if value else []


def _resolve_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off", ""}:
        return False
    raise ValueError(f"`{field_name}` must be a boolean.")


def _resolve_max_matches(args: dict[str, Any]) -> int:
    raw_value = args.get("max_matches", _DEFAULT_MAX_MATCHES)
    try:
        max_matches = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("`max_matches` must be an integer greater than 0.") from exc
    if max_matches <= 0:
        raise ValueError("`max_matches` must be an integer greater than 0.")
    return max_matches


def _iter_files(workspace: Workspace, paths: list[str] | None) -> list[str]:
    root = workspace.resolve(".")
    if not paths:
        return [str(path.relative_to(root)) for path in sorted(root.rglob("*")) if path.is_file()]

    results: list[str] = []
    for relative_path in paths:
        target = workspace.resolve(relative_path)
        if not target.exists():
            raise ValueError(f"Path not found: {relative_path}")
        if target.is_dir():
            results.extend(str(path.relative_to(root)) for path in sorted(target.rglob("*")) if path.is_file())
        else:
            results.append(str(target.relative_to(root)))
    return results


def _build_matcher(pattern: str, *, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled_pattern = pattern if regex else re.escape(pattern)
    try:
        return re.compile(compiled_pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid pattern: {exc}") from exc


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return "Missing required arg `pattern`."

    try:
        matcher = _build_matcher(
            pattern,
            regex=_resolve_bool(args.get("regex", False), field_name="regex"),
            case_sensitive=_resolve_bool(args.get("case_sensitive", False), field_name="case_sensitive"),
        )
        max_matches = _resolve_max_matches(args)
        paths = _resolve_paths(args)
        files = _iter_files(workspace, paths)
    except ValueError as exc:
        return str(exc)

    matches: list[str] = []
    files_checked = 0
    for relative_path in files:
        files_checked += 1
        try:
            content = workspace.read_text(relative_path)
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if not matcher.search(line):
                continue
            matches.append(f"{relative_path}:{line_number}:{line}")
            if len(matches) >= max_matches:
                break
        if len(matches) >= max_matches:
            break

    if not matches:
        scope = "workspace" if not paths else ", ".join(paths)
        return f"No matches for `{pattern}` in {scope}."

    lines = [
        f"Found {len(matches)} match(es) for `{pattern}` across {files_checked} file(s).",
        *matches,
    ]
    if len(matches) >= max_matches:
        lines.append(f"Stopped after reaching max_matches={max_matches}.")
    return "\n".join(lines)


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    return ActionEnvelope(
        name="search_files",
        args=args,
        reason="Search workspace files for matching text.",
        writes=[],
        requires_approval=False,
    )


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "build_action": build_action,
    }
