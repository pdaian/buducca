from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from assistant_framework import CollectorManager, CollectorRunner, CompressorManager, CompressorRunner, SkillManager, Workspace
from assistant_framework.traces import load_trace, replay_trace


def _read_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    try:
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _load_collector_config(path: str) -> dict:
    return _read_json(path).get("collectors", {})


def _load_compressor_config(path: str) -> dict:
    return _read_json(path).get("compressors", {})


def _run_collectors(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    workspace = Workspace(args.workspace)
    collector_config = _load_collector_config(args.config)
    manager = CollectorManager(args.collectors, config=collector_config)
    collectors = manager.load()

    CollectorRunner(workspace, collectors).run_forever()


def _run_compressors(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    workspace = Workspace(args.workspace)
    compressor_config = _load_compressor_config(args.config)
    manager = CompressorManager(args.compressors, config=compressor_config)
    compressors = manager.load()

    CompressorRunner(workspace, compressors).run_forever()


def _run_skill(args: argparse.Namespace) -> None:
    workspace = Workspace(args.workspace)
    skill_args = json.loads(args.args)

    skills = SkillManager(args.skills).load()
    if args.skill not in skills:
        raise SystemExit(f"Unknown skill '{args.skill}'. Available: {', '.join(sorted(skills))}")

    output = skills[args.skill].run(workspace, skill_args)
    print(output)


def _load_trace_section(args: argparse.Namespace) -> str:
    workspace = Workspace(args.workspace)
    trace = load_trace(workspace, args.trace)
    if not trace:
        return "No trace found."
    section = args.section
    if section == "last-turn":
        return json.dumps(trace, ensure_ascii=False, indent=2)
    value = trace.get(section.replace("-", "_"))
    if value is None:
        return f"Trace field not found: {section}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _show_trace(args: argparse.Namespace) -> None:
    print(_load_trace_section(args))


def _replay_trace(args: argparse.Namespace) -> None:
    workspace = Workspace(args.workspace)
    trace = load_trace(workspace, args.trace)
    print(replay_trace(trace))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assistant framework CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collectors_parser = subparsers.add_parser("collectors", help="Run background data collectors")
    collectors_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    collectors_parser.add_argument("--collectors", default="collectors", help="Collectors directory")
    collectors_parser.add_argument("--config", default="agent_config.json", help="Agent config JSON file")
    collectors_parser.set_defaults(handler=_run_collectors)

    compressors_parser = subparsers.add_parser("compressors", help="Run background workspace compressors")
    compressors_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    compressors_parser.add_argument("--compressors", default="compressors", help="Compressors directory")
    compressors_parser.add_argument("--config", default="compressors/config.json", help="Compressor config JSON file")
    compressors_parser.set_defaults(handler=_run_compressors)

    skill_parser = subparsers.add_parser("skill", help="Run a skill against the workspace")
    skill_parser.add_argument("skill", help="Skill name")
    skill_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    skill_parser.add_argument("--skills", default="skills", help="Skills directory")
    skill_parser.add_argument("--args", default="{}", help="JSON dict passed to the skill")
    skill_parser.set_defaults(handler=_run_skill)

    trace_parser = subparsers.add_parser("trace", help="Inspect or replay the last saved bot trace")
    trace_subparsers = trace_parser.add_subparsers(dest="trace_command", required=True)

    for name, section in (
        ("last-message", "last_message"),
        ("last-prompt", "last_prompt"),
        ("last-action", "last_action"),
        ("last-skill", "last_skill_result"),
        ("last-turn", "last-turn"),
    ):
        show_parser = trace_subparsers.add_parser(name, help=f"Show {name} from the latest trace")
        show_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
        show_parser.add_argument("--trace", default="", help="Optional explicit trace file path")
        show_parser.set_defaults(handler=_show_trace, section=section)

    replay_parser = trace_subparsers.add_parser("replay", help="Replay the latest saved bot trace")
    replay_parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    replay_parser.add_argument("--trace", default="", help="Optional explicit trace file path")
    replay_parser.set_defaults(handler=_replay_trace)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
