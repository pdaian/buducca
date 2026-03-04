#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from assistant_framework import SkillManager, Workspace


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a skill against the workspace")
    parser.add_argument("skill", help="Skill name")
    parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    parser.add_argument("--skills", default="skills", help="Skills directory")
    parser.add_argument("--args", default="{}", help="JSON dict passed to the skill")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Workspace(args.workspace)
    skill_args = json.loads(args.args)

    skills = SkillManager(args.skills).load()
    if args.skill not in skills:
        raise SystemExit(f"Unknown skill '{args.skill}'. Available: {', '.join(sorted(skills))}")

    output = skills[args.skill].run(workspace, skill_args)
    print(output)


if __name__ == "__main__":
    main()
