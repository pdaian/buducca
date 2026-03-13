from __future__ import annotations

from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.workspace import Workspace

NAME = "file_delete"
DESCRIPTION = (
    "Delete workspace files in bulk. "
    "Use args.paths as a list of file paths or args.path for a single file."
)
ARGS_SCHEMA = """
{
  paths?: string[];
  path?: string;
}
""".strip()


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


def _resolve_paths(workspace: Workspace, args: dict[str, Any]) -> list[str] | None:
    raw_paths = args.get("paths")
    if isinstance(raw_paths, list):
        paths = [_resolve_workspace_path(workspace, str(item).strip()) for item in raw_paths if str(item).strip()]
        return paths
    if raw_paths is not None:
        path = _resolve_workspace_path(workspace, str(raw_paths).strip())
        return [path] if path else []

    raw_path = str(args.get("path", "")).strip()
    if not raw_path:
        return None
    return [_resolve_workspace_path(workspace, raw_path)]


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    paths = _resolve_paths(workspace, args)
    if not paths:
        return "Missing required arg `paths` (list)."

    results: list[str] = []
    for path in paths:
        try:
            target = workspace.resolve(path)
        except ValueError as exc:
            results.append(str(exc))
            continue

        if not target.exists():
            results.append(f"{path}: File not found")
            continue
        if not target.is_file():
            results.append(f"{path}: Not a file")
            continue

        target.unlink()
        results.append(f"Deleted file: {path}")

    return "\n".join(results)


def build_action(args: dict[str, Any]) -> ActionEnvelope:
    return ActionEnvelope(
        name="file_delete.delete",
        args=args,
        reason="Delete files from the workspace.",
        writes=[],
        requires_approval=True,
    )


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "build_action": build_action,
    }
