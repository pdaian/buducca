from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.workspace import Workspace

NAME = "file"
DESCRIPTION = (
    "Manage workspace files and directories in bulk. "
    "Use args.action (or args.command) with: read/list/browse/write/append/move/copy/delete/"
    "replace_text/create_dir/delete_dir. "
    "Paths are workspace-relative. File actions accept args.path or args.paths. "
    "read supports full, head, tail, and range modes with read_mode/read_line_limit/start_line/end_line. "
    "move/copy support exact destinations via args.destinations or a shared args.destination_dir. "
    "replace_text updates one or more files using args.find and optional args.replace/regex/case_sensitive."
)
ARGS_SCHEMA = (
    '{"action":"read|list|browse|write|append|move|copy|delete|replace_text|create_dir|delete_dir",'
    '"path":"optional","paths":["optional"],"directories":["optional"],"contents":["optional"],'
    '"content":"optional","destination_dir":"optional","destinations":["optional"],'
    '"read_mode":"full|head|tail|range","read_line_limit":25,"start_line":1,"end_line":25,'
    '"recursive":false,"include_hidden":false,"max_entries":200,"find":"optional",'
    '"replace":"optional","regex":false,"case_sensitive":true,"max_replacements_per_file":0}'
)


def _normalize_workspace_path(path: str) -> str:
    normalized = path.strip()
    while "workspace/" in normalized:
        normalized = normalized.replace("workspace/", "")
    return normalized


def _normalize_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [_normalize_workspace_path(str(item).strip()) for item in value if str(item).strip()]
    item = _normalize_workspace_path(str(value).strip())
    return [item] if item else []


def _resolve_paths(args: dict[str, Any]) -> list[str] | None:
    paths = _normalize_list(args.get("paths"))
    if paths is not None:
        return paths
    path = _normalize_workspace_path(str(args.get("path", "")).strip())
    if not path:
        return None
    return [path]


def _resolve_contents(args: dict[str, Any], path_count: int) -> list[str] | None:
    contents = args.get("contents")
    if isinstance(contents, list):
        content_list = [str(item) for item in contents]
        if len(content_list) != path_count:
            raise ValueError("`contents` must have the same number of entries as `paths`.")
        return content_list
    if contents is not None:
        return [str(contents)] * path_count

    content = args.get("content")
    if content is not None:
        return [str(content)] * path_count
    return None


def _resolve_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off", ""}:
        return False
    raise ValueError(f"`{field_name}` must be a boolean.")


def _resolve_positive_int(value: Any, *, field_name: str) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"`{field_name}` must be an integer greater than 0.") from exc
    if resolved <= 0:
        raise ValueError(f"`{field_name}` must be an integer greater than 0.")
    return resolved


def _resolve_non_negative_int(value: Any, *, field_name: str, default: int = 0) -> int:
    raw_value = default if value is None else value
    try:
        resolved = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"`{field_name}` must be an integer greater than or equal to 0.") from exc
    if resolved < 0:
        raise ValueError(f"`{field_name}` must be an integer greater than or equal to 0.")
    return resolved


def _resolve_read_options(args: dict[str, Any]) -> tuple[str, int | None, int | None, int | None]:
    raw_mode = str(args.get("read_mode", "")).strip().lower()
    read_line_limit = args.get("read_line_limit")
    start_line = args.get("start_line")
    end_line = args.get("end_line")

    has_limit = read_line_limit is not None
    has_start = start_line is not None
    has_end = end_line is not None

    if raw_mode:
        if raw_mode not in {"full", "head", "tail", "range"}:
            raise ValueError("`read_mode` must be one of: full, head, tail, range.")
        mode = raw_mode
    elif has_start or has_end:
        mode = "range"
    elif has_limit:
        mode = "tail"
    else:
        mode = "full"

    limit = _resolve_positive_int(read_line_limit, field_name="read_line_limit") if has_limit else None
    start = _resolve_positive_int(start_line, field_name="start_line") if has_start else None
    end = _resolve_positive_int(end_line, field_name="end_line") if has_end else None

    if mode in {"head", "tail"} and limit is None:
        raise ValueError(f"`read_line_limit` is required for read_mode `{mode}`.")
    if mode == "range":
        if start is None and end is None:
            raise ValueError("`start_line` or `end_line` is required for read_mode `range`.")
        if start is not None and end is not None and end < start:
            raise ValueError("`end_line` must be greater than or equal to `start_line`.")
    elif start is not None or end is not None:
        raise ValueError("`start_line` and `end_line` can only be used with read_mode `range`.")

    return mode, limit, start, end


def _slice_content(content: str, *, mode: str, limit: int | None, start: int | None, end: int | None) -> str:
    if mode == "full":
        return content

    lines = content.splitlines()
    if mode == "head":
        return "\n".join(lines[: limit or 0])
    if mode == "tail":
        return "\n".join(lines[-(limit or 0) :])

    resolved_start = 1 if start is None else start
    if end is None and limit is not None:
        resolved_end = resolved_start + limit - 1
    else:
        resolved_end = len(lines) if end is None else end
    return "\n".join(lines[resolved_start - 1 : resolved_end])


def _is_hidden_relative(relative_path: Path) -> bool:
    return any(part.startswith(".") for part in relative_path.parts if part not in {"."})


def _format_browse_entry(relative_path: Path, *, is_dir: bool) -> str:
    entry = str(relative_path)
    if entry == ".":
        return "./"
    return f"{entry}/" if is_dir else entry


def _iter_browse_entries(
    target: Path,
    *,
    root: Path,
    recursive: bool,
    include_hidden: bool,
) -> list[str]:
    if target.is_file():
        relative = target.relative_to(root)
        if not include_hidden and _is_hidden_relative(relative):
            return []
        return [_format_browse_entry(relative, is_dir=False)]

    iterator = target.rglob("*") if recursive else target.iterdir()
    entries: list[tuple[int, str]] = []
    for entry in sorted(iterator):
        relative = entry.relative_to(root)
        if not include_hidden and _is_hidden_relative(relative):
            continue
        entries.append((0 if entry.is_dir() else 1, _format_browse_entry(relative, is_dir=entry.is_dir())))
    entries.sort(key=lambda item: (item[0], item[1]))
    return [item[1] for item in entries]


def _list(
    workspace: Workspace,
    paths: list[str] | None,
    *,
    recursive: bool,
    include_hidden: bool,
    max_entries: int,
) -> str:
    root = workspace.resolve(".")
    browse_paths = paths or ["."]
    entries: list[str] = []
    for raw_path in browse_paths:
        target = workspace.resolve(raw_path)
        if not target.exists():
            raise ValueError(f"Path not found: {raw_path}")
        entries.extend(
            _iter_browse_entries(
                target,
                root=root,
                recursive=recursive,
                include_hidden=include_hidden,
            )
        )
        if len(entries) >= max_entries:
            break

    displayed_entries = entries[:max_entries]
    scope = ", ".join(browse_paths)
    if not displayed_entries:
        return f"No entries found in {scope}."

    lines = [f"Browsing {scope}: showing {len(displayed_entries)} entrie(s).", *displayed_entries]
    if len(entries) > max_entries:
        lines.append(f"Stopped after reaching max_entries={max_entries}.")
    return "\n".join(lines)


def _read(
    workspace: Workspace,
    paths: list[str],
    *,
    mode: str,
    limit: int | None,
    start: int | None,
    end: int | None,
) -> str:
    results: list[str] = []
    for path in paths:
        target = workspace.resolve(path)
        if not target.exists():
            results.append(f"{path}: File not found")
            continue
        content = workspace.read_text(path)
        sliced = _slice_content(content, mode=mode, limit=limit, start=start, end=end)
        results.append(f"{path}:\n{sliced}")
    return "\n\n".join(results)


def _write(workspace: Workspace, paths: list[str], contents: list[str]) -> str:
    results: list[str] = []
    for path, content in zip(paths, contents):
        workspace.write_text(path, content)
        results.append(f"Wrote {len(content)} character(s) to {path}.")
    return "\n".join(results)


def _append(workspace: Workspace, paths: list[str], contents: list[str]) -> str:
    results: list[str] = []
    for path, content in zip(paths, contents):
        workspace.append_text(path, content)
        results.append(f"Appended {len(content)} character(s) to {path}.")
    return "\n".join(results)


def _create_dir(workspace: Workspace, directories: list[str]) -> str:
    for directory in directories:
        workspace.create_dir(directory)
    return "\n".join(f"Created directory: {directory}" for directory in directories)


def _delete_dir(workspace: Workspace, directories: list[str]) -> str:
    for directory in directories:
        workspace.delete_dir(directory)
    return "\n".join(f"Deleted directory: {directory}" for directory in directories)


def _resolve_destinations(args: dict[str, Any], path_count: int) -> tuple[list[str], str]:
    destination_dir = _normalize_workspace_path(str(args.get("destination_dir", "")).strip())
    destinations = _normalize_list(args.get("destinations"))

    if destination_dir and destinations:
        raise ValueError("Use either `destination_dir` or `destinations`, not both.")
    if destinations is not None:
        if len(destinations) != path_count:
            raise ValueError("`destinations` must have the same number of entries as `paths`.")
        return destinations, "exact"
    if destination_dir:
        return [destination_dir] * path_count, "directory"
    raise ValueError("Missing required arg `destination_dir` or `destinations`.")


def _move_or_copy(workspace: Workspace, paths: list[str], args: dict[str, Any], *, action: str) -> str:
    destinations, mode = _resolve_destinations(args, len(paths))
    results: list[str] = []
    for index, path in enumerate(paths):
        destination = destinations[index]
        if mode == "directory":
            source_name = Path(path).name
            destination = str(Path(destination) / source_name)
        if action == "move":
            final_path = workspace.move_path(path, destination)
            results.append(f"Moved {path} to {final_path}.")
        else:
            final_path = workspace.copy_path(path, destination)
            results.append(f"Copied {path} to {final_path}.")
    return "\n".join(results)


def _delete(workspace: Workspace, paths: list[str]) -> str:
    for path in paths:
        workspace.delete_path(path)
    return "\n".join(f"Deleted path: {path}" for path in paths)


def _replace_text(workspace: Workspace, paths: list[str], args: dict[str, Any]) -> str:
    find = str(args.get("find", ""))
    if not find:
        return "Missing required arg `find` for action `replace_text`."

    replace = str(args.get("replace", ""))
    regex = _resolve_bool(args.get("regex", False), field_name="regex")
    case_sensitive = _resolve_bool(args.get("case_sensitive", True), field_name="case_sensitive")
    max_replacements = _resolve_non_negative_int(
        args.get("max_replacements_per_file"),
        field_name="max_replacements_per_file",
        default=0,
    )

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern_text = find if regex else re.escape(find)
    try:
        matcher = re.compile(pattern_text, flags)
    except re.error as exc:
        return f"Invalid pattern: {exc}"

    results: list[str] = []
    replacement_count = 0 if max_replacements == 0 else max_replacements
    for path in paths:
        target = workspace.resolve(path)
        if not target.exists():
            results.append(f"{path}: File not found")
            continue
        if not target.is_file():
            results.append(f"{path}: Not a file")
            continue
        original = workspace.read_text(path)
        updated, count = matcher.subn(replace, original, count=replacement_count)
        workspace.write_text(path, updated)
        results.append(f"Replaced {count} occurrence(s) in {path}.")
    return "\n".join(results)


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    action_raw = args.get("action")
    if action_raw is None:
        action_raw = args.get("command", "read")
    action = str(action_raw).strip().lower()
    if action == "browse":
        action = "list"

    try:
        if action == "list":
            paths = _resolve_paths(args)
            recursive = _resolve_bool(args.get("recursive", False), field_name="recursive")
            include_hidden = _resolve_bool(args.get("include_hidden", False), field_name="include_hidden")
            max_entries = _resolve_positive_int(args.get("max_entries", 200), field_name="max_entries")
            return _list(
                workspace,
                paths,
                recursive=recursive,
                include_hidden=include_hidden,
                max_entries=max_entries,
            )

        if action in {"read", "write", "append", "move", "copy", "delete", "replace_text"}:
            paths = _resolve_paths(args)
            if not paths:
                return "Missing required arg `paths` (list)."

            if action == "read":
                mode, limit, start, end = _resolve_read_options(args)
                return _read(workspace, paths, mode=mode, limit=limit, start=start, end=end)

            if action in {"move", "copy"}:
                return _move_or_copy(workspace, paths, args, action=action)

            if action == "delete":
                return _delete(workspace, paths)

            if action == "replace_text":
                return _replace_text(workspace, paths, args)

            contents = _resolve_contents(args, len(paths))
            if contents is None:
                return "Missing required arg `contents` (list) for action `write`/`append`."

            if action == "write":
                return _write(workspace, paths, contents)
            return _append(workspace, paths, contents)

        if action in {"create_dir", "delete_dir"}:
            directories = _normalize_list(args.get("directories"))
            if not directories:
                return "Missing required arg `directories` (list)."
            if action == "create_dir":
                return _create_dir(workspace, directories)
            return _delete_dir(workspace, directories)
    except ValueError as exc:
        return str(exc)

    return (
        "Unsupported action. Use one of: read, list, browse, write, append, move, copy, delete, "
        "replace_text, create_dir, delete_dir."
    )


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    action_raw = args.get("action")
    if action_raw is None:
        action_raw = args.get("command", "read")
    action = str(action_raw).strip().lower()
    if action == "browse":
        action = "list"

    writes: list[str] = []
    if action in {"write", "append", "delete", "replace_text"}:
        writes = _resolve_paths(args) or []
    elif action in {"move", "copy"}:
        writes = _resolve_paths(args) or []
        destination_dir = _normalize_workspace_path(str(args.get("destination_dir", "")).strip())
        destinations = _normalize_list(args.get("destinations")) or []
        if destination_dir:
            writes.append(destination_dir)
        writes.extend(destinations)
    elif action in {"create_dir", "delete_dir"}:
        writes = _normalize_list(args.get("directories")) or []

    return ActionEnvelope(
        name=f"file.{action}",
        args=args,
        reason=f"Run file skill action `{action}`.",
        writes=writes,
        requires_approval=action != "read" and action != "list",
    )


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "build_action": build_action,
    }
