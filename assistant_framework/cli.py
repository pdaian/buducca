from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from assistant_framework import CollectorManager, CollectorRunner, SkillManager, Workspace


def _read_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    try:
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _load_collector_config(path: str) -> dict:
    return _read_json(path).get("collectors", {})


def _load_bot_config(path: str) -> dict:
    return _read_json(path)


def _command_ignores_attachments(command: Any) -> bool:
    return isinstance(command, list) and "--ignore-attachments" in command


def _signal_ignore_attachments_enabled(signal_collector_cfg: dict[str, Any]) -> bool:
    global_ignore = bool(signal_collector_cfg.get("ignore_attachments", True))

    if _command_ignores_attachments(signal_collector_cfg.get("command")):
        return True

    accounts = signal_collector_cfg.get("accounts")
    if isinstance(accounts, list) and accounts:
        for account in accounts:
            if not isinstance(account, dict):
                continue
            if _command_ignores_attachments(account.get("command")):
                return True
            if bool(account.get("ignore_attachments", global_ignore)):
                return True
        return False

    return global_ignore


def _signal_overlap_accounts(signal_collector_cfg: dict[str, Any], signal_frontend_account: str) -> list[str]:
    overlaps: list[str] = []
    accounts = signal_collector_cfg.get("accounts")
    if isinstance(accounts, list) and accounts:
        for account in accounts:
            if not isinstance(account, dict):
                continue
            device_name = str(account.get("device_name") or "")
            if device_name and device_name == signal_frontend_account:
                overlaps.append(str(account.get("name") or "default"))
        return overlaps

    configured_device = str(signal_collector_cfg.get("device_name") or "")
    if configured_device and configured_device == signal_frontend_account:
        overlaps.append(str(signal_collector_cfg.get("account_name") or "default"))
    return overlaps


def _warn_signal_attachment_mismatch(collector_config: dict[str, Any], bot_config: dict[str, Any]) -> None:
    signal_frontend = bot_config.get("signal")
    if not isinstance(signal_frontend, dict):
        return

    signal_collector_cfg = collector_config.get("signal_messages")
    if not isinstance(signal_collector_cfg, dict):
        return

    if not _signal_ignore_attachments_enabled(signal_collector_cfg):
        return

    signal_account = str(signal_frontend.get("account") or "")
    overlaps = _signal_overlap_accounts(signal_collector_cfg, signal_account)
    if overlaps:
        logging.warning(
            "Signal collector is configured with --ignore-attachments while Signal frontend is enabled "
            "for the same account (%s). Shared-account mode can lose voice/attachment context and is "
            "unsupported unless you explicitly disable ignore_attachments or override the receive command.",
            signal_account,
        )
        return

    logging.warning(
        "Signal collector is configured with --ignore-attachments while Signal frontend is enabled. "
        "This can lose voice/attachment context.",
    )


def _run_collectors(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    workspace = Workspace(args.workspace)
    collector_config = _load_collector_config(args.config)
    bot_config = _load_bot_config(args.bot_config)
    _warn_signal_attachment_mismatch(collector_config, bot_config)

    manager = CollectorManager(args.collectors, config=collector_config)
    collectors = manager.load()

    CollectorRunner(workspace, collectors).run_forever()


def _run_skill(args: argparse.Namespace) -> None:
    workspace = Workspace(args.workspace)
    skill_args = json.loads(args.args)

    skills = SkillManager(args.skills).load()
    if args.skill not in skills:
        raise SystemExit(f"Unknown skill '{args.skill}'. Available: {', '.join(sorted(skills))}")

    output = skills[args.skill].run(workspace, skill_args)
    print(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assistant framework CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collectors_parser = subparsers.add_parser("collectors", help="Run background data collectors")
    collectors_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    collectors_parser.add_argument("--collectors", default="collectors", help="Collectors directory")
    collectors_parser.add_argument("--config", default="agent_config.json", help="Agent config JSON file")
    collectors_parser.add_argument(
        "--bot-config",
        default="config.json",
        help="Bot config JSON file used for Signal collision warnings",
    )
    collectors_parser.set_defaults(handler=_run_collectors)

    skill_parser = subparsers.add_parser("skill", help="Run a skill against the workspace")
    skill_parser.add_argument("skill", help="Skill name")
    skill_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    skill_parser.add_argument("--skills", default="skills", help="Skills directory")
    skill_parser.add_argument("--args", default="{}", help="JSON dict passed to the skill")
    skill_parser.set_defaults(handler=_run_skill)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
