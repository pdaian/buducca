#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging

from assistant_framework import CollectorManager, CollectorRunner, Workspace


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run background data collectors")
    parser.add_argument("--workspace", default="workspace", help="Workspace directory")
    parser.add_argument("--collectors", default="collectors", help="Collectors directory")
    parser.add_argument("--config", default="agent_config.json", help="Agent config JSON file")
    return parser.parse_args()


def load_collector_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    return raw.get("collectors", {})


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    workspace = Workspace(args.workspace)
    collector_config = load_collector_config(args.config)
    manager = CollectorManager(args.collectors, config=collector_config)
    collectors = manager.load()

    CollectorRunner(workspace, collectors).run_forever()


if __name__ == "__main__":
    main()
