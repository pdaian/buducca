#!/usr/bin/env python3
"""Reset local workspace data and other ephemeral files.

This script is intended for local development cleanup. It removes the runtime
workspace directory and common cache/temp artifacts produced by test and Python
runs.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DEFAULT_WORKSPACE = "workspace"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _safe_within_repo(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _gather_targets(repo_root: Path) -> list[Path]:
    config = _load_json(repo_root / "config.json")
    runtime = config.get("runtime", {}) if isinstance(config, dict) else {}
    workspace_dir = Path(runtime.get("workspace_dir", DEFAULT_WORKSPACE))

    agent_config = _load_json(repo_root / "agent_config.json")
    collector_cfg = {}
    if isinstance(agent_config, dict):
        collector_cfg = (
            agent_config.get("collectors", {})
            .get("telegram_recent", {}) or agent_config.get("collectors", {}).get("telegram_recent_collector", {})
            .get("user_client", {})
        )
    session_path = collector_cfg.get("session_path")

    targets: set[Path] = set()

    ws_target = (repo_root / workspace_dir).resolve()
    if _safe_within_repo(repo_root, ws_target):
        targets.add(ws_target)

    if isinstance(session_path, str) and session_path.strip():
        session_target = (repo_root / session_path).resolve()
        if _safe_within_repo(repo_root, session_target):
            targets.add(session_target)

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
