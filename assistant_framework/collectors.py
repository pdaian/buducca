from __future__ import annotations

import importlib.util
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .workspace import Workspace

COLLECTOR_STATUS_FILE = "collector_status.json"


@dataclass
class Collector:
    name: str
    interval_seconds: float
    run: Callable[[Workspace], None]


class CollectorManager:
    def __init__(self, collectors_dir: str | Path, config: dict[str, Any] | None = None) -> None:
        self.collectors_dir = Path(collectors_dir)
        self.config = config or {}

    def _load_module(self, path: Path) -> ModuleType:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load collector module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def load(self) -> list[Collector]:
        collectors: list[Collector] = []
        if not self.collectors_dir.exists():
            return collectors

        for file_path in sorted(self.collectors_dir.glob("*.py")):
            if file_path.name.startswith("_"):
                continue
            module = self._load_module(file_path)
            collectors.append(self._build_collector(module, file_path))
        return collectors

    def _build_collector(self, module: ModuleType, file_path: Path) -> Collector:
        if hasattr(module, "create_collector"):
            cfg = self.config.get(file_path.stem, {})
            collector = module.create_collector(cfg)
            return Collector(
                name=collector["name"],
                interval_seconds=float(collector.get("interval_seconds", 60.0)),
                run=collector["run"],
            )

        run = getattr(module, "run", None)
        if run is None or not callable(run):
            raise RuntimeError(f"Collector file {file_path} must expose run(workspace)")

        name = getattr(module, "NAME", file_path.stem)
        interval_seconds = float(getattr(module, "INTERVAL_SECONDS", 60.0))
        return Collector(name=name, interval_seconds=interval_seconds, run=run)


class CollectorRunner:
    def __init__(self, workspace: Workspace, collectors: list[Collector], status_file: str = COLLECTOR_STATUS_FILE) -> None:
        self.workspace = workspace
        self.collectors = collectors
        self.status_file = status_file
        self.started_at = datetime.now(timezone.utc)
        self.loop_count = 0
        self._stats = {
            collector.name: {
                "runs": 0,
                "failures": 0,
                "last_success_at": None,
                "last_error_at": None,
                "last_error": None,
                "interval_seconds": collector.interval_seconds,
            }
            for collector in collectors
        }

    def _write_status_snapshot(self) -> None:
        snapshot = {
            "runner_started_at": self.started_at.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "collector_count": len(self.collectors),
            "loop_count": self.loop_count,
            "collectors": self._stats,
        }
        self.workspace.write_text(self.status_file, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")

    def run_once(self, next_run: dict[str, float], now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        self.loop_count += 1
        for collector in self.collectors:
            if ts < next_run[collector.name]:
                continue
            try:
                collector.run(self.workspace)
                self._stats[collector.name]["runs"] += 1
                self._stats[collector.name]["last_success_at"] = datetime.now(timezone.utc).isoformat()
                self._stats[collector.name]["last_error"] = None
                logging.info("Collector %s finished", collector.name)
            except Exception as err:
                self._stats[collector.name]["failures"] += 1
                self._stats[collector.name]["last_error_at"] = datetime.now(timezone.utc).isoformat()
                self._stats[collector.name]["last_error"] = str(err)
                logging.exception("Collector %s failed", collector.name)
            next_run[collector.name] = ts + collector.interval_seconds

        self._write_status_snapshot()

    def run_forever(self) -> None:
        if not self.collectors:
            logging.warning("No collectors loaded; exiting")
            return

        next_run = {collector.name: 0.0 for collector in self.collectors}
        logging.info("Collector runner started with %s collectors", len(self.collectors))

        while True:
            self.run_once(next_run)
            time.sleep(1)
