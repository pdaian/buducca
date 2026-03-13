from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
import re
from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.workspace import Workspace

NAME = "search_files"
DESCRIPTION = (
    "Search workspace text files with literal or regex matching across a single file, "
    "a list of files, or a directory tree. "
    "Args: pattern (required), path or paths (optional file or directory scope), "
    "regex (optional bool), case_sensitive (optional bool), max_matches (optional int, default 50), "
    "context_lines (optional int), file_pattern (optional glob or list of globs), "
    "and include_hidden (optional bool)."
)
ARGS_SCHEMA = (
    '{"pattern":"required","path":"optional/file/or/dir","paths":["optional/file/or/dir"],'
    '"regex":false,"case_sensitive":false,"max_matches":50,"context_lines":0,'
    '"file_pattern":"optional/*.py/or/list","include_hidden":false}'
)

_DEFAULT_MAX_MATCHES = 50
_ATTACHMENTS_ROOT = "attachments"


def _normalize_workspace_path(path: str) -> str:
    return path.strip()


def _strip_workspace_components(path: str) -> str:
    return "/".join(part for part in path.split("/") if part and part != "workspace")


def _resolve_workspace_path(workspace: Workspace, path: str) -> str:
    normalized = _normalize_workspace_path(path)
    stripped = _strip_workspace_components(normalized)
    if not stripped or stripped == normalized:
        return normalized
    original_target = workspace.resolve(normalized)
    if original_target.exists() or original_target.parent.exists():
        return normalized
    return stripped


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


def _resolve_non_negative_int(value: Any, *, field_name: str, default: int) -> int:
    raw_value = default if value is None else value
    try:
        resolved = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"`{field_name}` must be an integer greater than or equal to 0.") from exc
    if resolved < 0:
        raise ValueError(f"`{field_name}` must be an integer greater than or equal to 0.")
    return resolved


def _resolve_file_patterns(args: dict[str, Any]) -> list[str] | None:
    raw_value = args.get("file_pattern")
    if raw_value is None:
        return None
    if isinstance(raw_value, list):
        patterns = [str(item).strip() for item in raw_value if str(item).strip()]
    else:
        patterns = [str(raw_value).strip()]
    return patterns or None


def _is_hidden_relative(relative_path: Path) -> bool:
    return any(part.startswith(".") for part in relative_path.parts if part not in {"."})


def _is_attachment_relative(relative_path: Path) -> bool:
    return relative_path.parts[:1] == (_ATTACHMENTS_ROOT,)


def _matches_file_patterns(relative_path: str, file_patterns: list[str] | None) -> bool:
    if not file_patterns:
        return True
    path_name = Path(relative_path).name
    return any(fnmatch(relative_path, pattern) or fnmatch(path_name, pattern) for pattern in file_patterns)


def _iter_files(
    workspace: Workspace,
    paths: list[str] | None,
    *,
    include_hidden: bool,
    file_patterns: list[str] | None,
) -> list[str]:
    root = workspace.resolve(".")
    resolved_paths = None if paths is None else [_resolve_workspace_path(workspace, path) for path in paths]
    if not resolved_paths:
        results: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if _is_attachment_relative(relative):
                continue
            if not include_hidden and _is_hidden_relative(relative):
                continue
            relative_str = str(relative)
            if _matches_file_patterns(relative_str, file_patterns):
                results.append(relative_str)
        return results

    results: list[str] = []
    for relative_path in resolved_paths:
        target = workspace.resolve(relative_path)
        if not target.exists():
            raise ValueError(f"Path not found: {relative_path}")
        if target.is_dir():
            for path in sorted(target.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(root)
                if _is_attachment_relative(relative):
                    continue
                if not include_hidden and _is_hidden_relative(relative):
                    continue
                relative_str = str(relative)
                if _matches_file_patterns(relative_str, file_patterns):
                    results.append(relative_str)
        else:
            relative = target.relative_to(root)
            if _is_attachment_relative(relative):
                continue
            if not include_hidden and _is_hidden_relative(relative):
                continue
            relative_str = str(relative)
            if _matches_file_patterns(relative_str, file_patterns):
                results.append(relative_str)
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
        include_hidden = _resolve_bool(args.get("include_hidden", False), field_name="include_hidden")
        context_lines = _resolve_non_negative_int(args.get("context_lines"), field_name="context_lines", default=0)
        file_patterns = _resolve_file_patterns(args)
        matcher = _build_matcher(
            pattern,
            regex=_resolve_bool(args.get("regex", False), field_name="regex"),
            case_sensitive=_resolve_bool(args.get("case_sensitive", False), field_name="case_sensitive"),
        )
        max_matches = _resolve_max_matches(args)
        paths = _resolve_paths(args)
        files = _iter_files(workspace, paths, include_hidden=include_hidden, file_patterns=file_patterns)
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
        lines = content.splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not matcher.search(line):
                continue
            if context_lines > 0:
                start = max(0, line_number - 1 - context_lines)
                end = min(len(lines), line_number + context_lines)
                block = [f"{relative_path}:{line_number}:{line}"]
                for context_index in range(start, end):
                    current_line_number = context_index + 1
                    if current_line_number == line_number:
                        continue
                    block.append(f"{relative_path}-{current_line_number}-{lines[context_index]}")
                matches.append("\n".join(block))
            else:
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
