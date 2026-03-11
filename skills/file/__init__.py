from __future__ import annotations

from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.workspace import Workspace

NAME = "file"
DESCRIPTION = (
    "Manage workspace files and directories in bulk. "
    "Use args.action (or args.command) with one of: read/write/append/create_dir/delete_dir/move. "
    "File actions require args.paths as a list. write/append also require args.contents as a list "
    "(or args.content to apply the same text to every path). "
    "read accepts optional args.read_line_limit to return only the last N lines. "
    "move requires args.paths and args.destination_dir. "
    "Directory actions require args.directories as a list."
)


def _normalize_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return cleaned
    item = str(value).strip()
    if not item:
        return []
    return [item]


def _resolve_paths(args: dict[str, Any]) -> list[str] | None:
    paths = _normalize_list(args.get("paths"))
    if paths is not None:
        return paths
    path = str(args.get("path", "")).strip()
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


def _resolve_read_line_limit(args: dict[str, Any]) -> int | None:
    read_line_limit = args.get("read_line_limit")
    if read_line_limit is None:
        return None

    try:
        limit = int(read_line_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("`read_line_limit` must be an integer greater than 0.") from exc

    if limit <= 0:
        raise ValueError("`read_line_limit` must be an integer greater than 0.")
    return limit


def _tail_lines(content: str, limit: int) -> str:
    lines = content.splitlines()
    return "\n".join(lines[-limit:])


def _read(workspace: Workspace, paths: list[str], read_line_limit: int | None = None) -> str:
    results: list[str] = []
    for path in paths:
        target = workspace.resolve(path)
        if not target.exists():
            results.append(f"{path}: File not found")
            continue
        content = workspace.read_text(path)
        if read_line_limit is not None:
            content = _tail_lines(content, read_line_limit)
        results.append(f"{path}:\n{content}")
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


def _move(workspace: Workspace, paths: list[str], destination_dir: str) -> str:
    results: list[str] = []
    for path in paths:
        destination = workspace.move_file_to_dir(path, destination_dir)
        results.append(f"Moved {path} to {destination}.")
    return "\n".join(results)


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    action_raw = args.get("action")
    if action_raw is None:
        action_raw = args.get("command", "read")
    action = str(action_raw).strip().lower()

    try:
        if action in {"read", "write", "append", "move"}:
            paths = _resolve_paths(args)
            if not paths:
                return "Missing required arg `paths` (list)."

            if action == "read":
                read_line_limit = _resolve_read_line_limit(args)
                return _read(workspace, paths, read_line_limit)

            if action == "move":
                destination_dir = str(args.get("destination_dir", "")).strip()
                if not destination_dir:
                    return "Missing required arg `destination_dir` for action `move`."
                return _move(workspace, paths, destination_dir)

            contents = _resolve_contents(args, len(paths))
            if contents is None:
                return "Missing required arg `contents` (list) for action `write`/`append`."

            if action == "write":
                return _write(workspace, paths, contents)
            if action == "append":
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

    return "Unsupported action. Use one of: read, write, append, create_dir, delete_dir, move."


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    action_raw = args.get("action")
    if action_raw is None:
        action_raw = args.get("command", "read")
    action = str(action_raw).strip().lower()
    writes: list[str] = []
    if action in {"write", "append", "move"}:
        writes = _resolve_paths(args) or []
    elif action in {"create_dir", "delete_dir"}:
        writes = _normalize_list(args.get("directories")) or []
    return ActionEnvelope(
        name=f"file.{action}",
        args=args,
        reason=f"Run file skill action `{action}`.",
        writes=writes,
        requires_approval=action != "read",
    )
