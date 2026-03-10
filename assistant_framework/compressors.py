from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .module_loader import iter_plugin_modules, load_module_from_file
from .workspace import Workspace

COMPRESSOR_STATUS_FILE = "compressor_status.json"


@dataclass
class Compressor:
    name: str
    interval_seconds: float
    run: Callable[[Workspace], None]


class CompressorManager:
    def __init__(self, compressors_dir: str | Path, config: dict[str, Any] | None = None) -> None:
        self.compressors_dir = Path(compressors_dir)
        self.config = config or {}

    def _load_module(self, path: Path) -> ModuleType:
        return load_module_from_file(path, kind="compressor")

    def _iter_module_files(self) -> list[Path]:
        return iter_plugin_modules(self.compressors_dir)

    def load(self) -> list[Compressor]:
        compressors: list[Compressor] = []
        if not self.compressors_dir.exists():
            return compressors

        for file_path in self._iter_module_files():
            module = self._load_module(file_path)
            compressors.append(self._build_compressor(module, file_path))
        return compressors

    def _build_compressor(self, module: ModuleType, file_path: Path) -> Compressor:
        if hasattr(module, "create_compressor"):
            config_key = file_path.parent.name if file_path.name == "__init__.py" else file_path.stem
            cfg = self.config.get(config_key, {})
            if not cfg and not config_key.endswith("_compressor"):
                cfg = self.config.get(f"{config_key}_compressor", {})
            compressor = module.create_compressor(cfg)
            return Compressor(
                name=compressor["name"],
                interval_seconds=float(compressor.get("interval_seconds", 60.0)),
                run=compressor["run"],
            )

        run = getattr(module, "run", None)
        if run is None or not callable(run):
            raise RuntimeError(f"Compressor file {file_path} must expose run(workspace)")

        name = getattr(module, "NAME", file_path.stem)
        interval_seconds = float(getattr(module, "INTERVAL_SECONDS", 60.0))
        return Compressor(name=name, interval_seconds=interval_seconds, run=run)


class CompressorRunner:
    def __init__(self, workspace: Workspace, compressors: list[Compressor], status_file: str = COMPRESSOR_STATUS_FILE) -> None:
        self.workspace = workspace
        self.compressors = compressors
        self.status_file = status_file
        self.started_at = datetime.now(timezone.utc)
        self.loop_count = 0
        self._stats = {
            compressor.name: {
                "runs": 0,
                "failures": 0,
                "last_success_at": None,
                "last_error_at": None,
                "last_error": None,
                "interval_seconds": compressor.interval_seconds,
            }
            for compressor in compressors
        }

    def _write_status_snapshot(self) -> None:
        snapshot = {
            "runner_started_at": self.started_at.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "compressor_count": len(self.compressors),
            "loop_count": self.loop_count,
            "compressors": self._stats,
        }
        self.workspace.write_text(self.status_file, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")

    def run_once(self, next_run: dict[str, float], now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        self.loop_count += 1
        for compressor in self.compressors:
            if ts < next_run[compressor.name]:
                continue
            try:
                compressor.run(self.workspace)
                self._stats[compressor.name]["runs"] += 1
                self._stats[compressor.name]["last_success_at"] = datetime.now(timezone.utc).isoformat()
                self._stats[compressor.name]["last_error"] = None
                logging.info("Compressor %s finished", compressor.name)
            except Exception as err:
                self._stats[compressor.name]["failures"] += 1
                self._stats[compressor.name]["last_error_at"] = datetime.now(timezone.utc).isoformat()
                self._stats[compressor.name]["last_error"] = str(err)
                logging.exception("Compressor %s failed", compressor.name)
            next_run[compressor.name] = ts + compressor.interval_seconds

        self._write_status_snapshot()

    def run_forever(self) -> None:
        if not self.compressors:
            logging.warning("No compressors loaded; exiting")
            return

        next_run = {compressor.name: 0.0 for compressor in self.compressors}
        logging.info("Compressor runner started with %s compressors", len(self.compressors))

        while True:
            self.run_once(next_run)
            time.sleep(1)
