from __future__ import annotations

import argparse
import json
import logging

from assistant_framework import CollectorManager, CollectorRunner, SkillManager, Workspace


def _load_collector_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    return raw.get("collectors", {})


def _run_collectors(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    workspace = Workspace(args.workspace)
    collector_config = _load_collector_config(args.config)
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
