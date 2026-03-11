#!/usr/bin/env python3
"""Reset local workspace data and other ephemeral files.

This script is intended for local development cleanup. It removes the runtime
workspace directory and common cache/temp artifacts produced by test and Python
runs.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from assistant_framework.config_files import load_json_path, load_named_config_map


DEFAULT_WORKSPACE = "workspace"


def _load_json(path: Path) -> dict:
    try:
        payload = load_json_path(path)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_optional_named_map(path: Path, *, section_name: str | None = None) -> dict:
    try:
        return load_named_config_map(path, section_name=section_name)
    except ValueError:
        return {}


def _safe_within_repo(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _add_repo_target(targets: set[Path], repo_root: Path, path: Path) -> None:
    resolved = path.resolve()
    if _safe_within_repo(repo_root, resolved):
        targets.add(resolved)


def _add_telegram_session_targets(targets: set[Path], repo_root: Path, session_path: str) -> None:
    if not isinstance(session_path, str) or not session_path.strip():
        return

    base_target = (repo_root / session_path).resolve()
    _add_repo_target(targets, repo_root, base_target)

    if base_target.suffix == ".session":
        session_file = base_target
    else:
        session_file = base_target.with_suffix(f"{base_target.suffix}.session") if base_target.suffix else base_target.with_suffix(".session")
    _add_repo_target(targets, repo_root, session_file)
    _add_repo_target(targets, repo_root, session_file.with_name(f"{session_file.name}-journal"))

    if base_target.suffix:
        state_target = base_target.with_suffix(f"{base_target.suffix}.updates.json")
    else:
        state_target = base_target.with_suffix(".updates.json")
    _add_repo_target(targets, repo_root, state_target)


def _gather_targets(repo_root: Path) -> list[Path]:
    config = _load_json(repo_root / "config.json")
    runtime = config.get("runtime", {}) if isinstance(config, dict) else {}
    workspace_dir = Path(runtime.get("workspace_dir", DEFAULT_WORKSPACE))
    collector_config_path = runtime.get("collector_config_path", "config/collectors")

    agent_config = (
        _load_optional_named_map(repo_root / collector_config_path, section_name="collectors")
        if isinstance(collector_config_path, str) and collector_config_path.strip()
        else {}
    )
    collector_cfg = {}
    if isinstance(agent_config, dict):
        telegram_recent = agent_config.get("telegram_recent", {})
        if not isinstance(telegram_recent, dict) or not telegram_recent:
            telegram_recent = agent_config.get("telegram_recent_collector", {})
        if isinstance(telegram_recent, dict):
            collector_cfg = telegram_recent.get("user_client", {}) or {}
    session_path = collector_cfg.get("session_path")

    targets: set[Path] = set()

    _add_repo_target(targets, repo_root, repo_root / workspace_dir)

    _add_repo_target(targets, repo_root, repo_root / "data")

    for relative_path in (
        "telegram_user.session",
        "telegram_user.session-journal",
        "telegram_user.updates.json",
    ):
        _add_repo_target(targets, repo_root, repo_root / relative_path)

    telegram_cfg = config.get("telegram", {}) if isinstance(config, dict) else {}
    if isinstance(telegram_cfg, dict):
        _add_telegram_session_targets(targets, repo_root, telegram_cfg.get("session_path", ""))

    _add_telegram_session_targets(targets, repo_root, session_path)

    # Common local ephemeral artifacts.
    for pattern in (
        "**/__pycache__",
        "**/*.pyc",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".coverage",
    ):
        for path in repo_root.glob(pattern):
            if _safe_within_repo(repo_root, path):
                targets.add(path.resolve())

    return sorted(targets)


def _delete_path(path: Path, dry_run: bool) -> tuple[bool, str]:
    if not path.exists() and not path.is_symlink():
        return False, "missing"

    if dry_run:
        return True, "dry-run"

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return True, "directory"

    path.unlink(missing_ok=True)
    return True, "file"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete workspace output and other ephemeral local data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting anything.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the safety prompt.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    targets = _gather_targets(repo_root)

    if not targets:
        print("Nothing to clean.")
        return 0

    print("Cleanup targets:")
    for target in targets:
        print(f" - {target.relative_to(repo_root)}")

    if not args.dry_run and not args.yes:
        answer = input("\nDelete these files/directories? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Aborted.")
            return 1

    removed = 0
    skipped = 0
    for target in targets:
        ok, status = _delete_path(target, dry_run=args.dry_run)
        if ok:
            removed += 1
            print(f"removed: {target.relative_to(repo_root)} ({status})")
        else:
            skipped += 1
            print(f"skipped: {target.relative_to(repo_root)} ({status})")

    print(f"\nDone. removed={removed} skipped={skipped} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
