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
from .plugin_logging import log_plugin_event
from .workspace import Workspace

COLLECTOR_STATUS_FILE = "collector_status.json"


@dataclass
class Collector:
    name: str
    description: str
    interval_seconds: float
    run: Callable[[Workspace], None]
    generated_files: list[str]
    module_files: list[str]


@dataclass
class CollectorManifest:
    name: str
    description: str
    file_structure: list[str]
    generated_files: list[str]


@dataclass
class CollectorRegistration:
    collector: Collector
    manifest: CollectorManifest
    config_key: str


class CollectorManager:
    def __init__(self, collectors_dir: str | Path, config: dict[str, Any] | None = None) -> None:
        self.collectors_dir = Path(collectors_dir)
        self.config = config or {}

    def _load_module(self, path: Path):
        return load_module_from_file(path, kind="collector")

    def _iter_module_files(self) -> list[Path]:
        return iter_plugin_modules(self.collectors_dir)

    def _config_for_module(self, file_path: Path) -> tuple[str, dict[str, Any]]:
        config_key = file_path.parent.name if file_path.name == "__init__.py" else file_path.stem
        cfg = self.config.get(config_key, {})
        if not cfg and not config_key.endswith("_collector"):
            cfg = self.config.get(f"{config_key}_collector", {})
        if not isinstance(cfg, dict):
            cfg = {}
        return config_key, cfg

    @staticmethod
    def _is_enabled(config: dict[str, Any]) -> bool:
        return bool(config.get("enabled", True))

    def _build_registration(self, module: ModuleType, file_path: Path, config: dict[str, Any]) -> CollectorRegistration:
        default_name = file_path.parent.name if file_path.name == "__init__.py" else file_path.stem
        readme_path = file_path.parent / "README.md"
        default_structure = [str(file_path.as_posix())]
        if readme_path.exists():
            default_structure.append(str(readme_path.as_posix()))

        if hasattr(module, "register_collector"):
            registered = module.register_collector(config)
            name = str(registered.get("name") or default_name)
            description = str(registered.get("description") or getattr(module, "DESCRIPTION", "")).strip()
            generated_files = [str(item) for item in registered.get("generated_files", getattr(module, "GENERATED_FILES", []))]
            file_structure = [str(item) for item in registered.get("file_structure", getattr(module, "FILE_STRUCTURE", default_structure))]
            run = registered.get("run")
            if run is None or not callable(run):
                raise RuntimeError(f"Collector file {file_path} register_collector(config) must return callable run(workspace)")
            interval_seconds = float(registered.get("interval_seconds", getattr(module, "INTERVAL_SECONDS", 60.0)))
        elif hasattr(module, "create_collector"):
            collector = module.create_collector(config)
            name = str(collector.get("name") or getattr(module, "NAME", default_name))
            description = str(collector.get("description") or getattr(module, "DESCRIPTION", "")).strip()
            generated_files = [str(item) for item in collector.get("generated_files", getattr(module, "GENERATED_FILES", []))]
            file_structure = [str(item) for item in collector.get("file_structure", getattr(module, "FILE_STRUCTURE", default_structure))]
            run = collector.get("run")
            if run is None or not callable(run):
                raise RuntimeError(f"Collector file {file_path} create_collector(config) must return callable run(workspace)")
            interval_seconds = float(collector.get("interval_seconds", getattr(module, "INTERVAL_SECONDS", 60.0)))
        else:
            run = getattr(module, "run", None)
            if run is None or not callable(run):
                raise RuntimeError(f"Collector file {file_path} must expose run(workspace)")
            name = getattr(module, "NAME", default_name)
            description = str(getattr(module, "DESCRIPTION", "")).strip()
            generated_files = [str(item) for item in getattr(module, "GENERATED_FILES", [])]
            file_structure = [str(item) for item in getattr(module, "FILE_STRUCTURE", default_structure)]
            interval_seconds = float(getattr(module, "INTERVAL_SECONDS", 60.0))

        collector = Collector(
            name=name,
            description=description,
            interval_seconds=interval_seconds,
            run=run,
            generated_files=generated_files,
            module_files=file_structure,
        )
        manifest = CollectorManifest(name=name, description=description, file_structure=file_structure, generated_files=generated_files)
        config_key, _ = self._config_for_module(file_path)
        return CollectorRegistration(collector=collector, manifest=manifest, config_key=config_key)

    def load_registrations(self) -> list[CollectorRegistration]:
        registrations: list[CollectorRegistration] = []
        if not self.collectors_dir.exists():
            return registrations

        for file_path in self._iter_module_files():
            config_key, config = self._config_for_module(file_path)
            if not self._is_enabled(config):
                log_plugin_event("collector", config_key, "skipped", reason="disabled")
                continue
            try:
                module = self._load_module(file_path)
                registration = self._build_registration(module, file_path, config)
            except Exception as exc:
                logging.warning("collector=%s event=skipped reason=load_failed error=%s", config_key, exc)
                continue
            registrations.append(registration)
        return registrations

    def load(self) -> list[Collector]:
        return [registration.collector for registration in self.load_registrations()]

    def load_manifests(self) -> list[CollectorManifest]:
        return [registration.manifest for registration in self.load_registrations()]


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
                "description": collector.description,
                "generated_files": collector.generated_files,
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
                log_plugin_event("collector", collector.name, "run_started", interval_seconds=collector.interval_seconds)
                collector.run(self.workspace)
                self._stats[collector.name]["runs"] += 1
                self._stats[collector.name]["last_success_at"] = datetime.now(timezone.utc).isoformat()
                self._stats[collector.name]["last_error"] = None
                log_plugin_event("collector", collector.name, "run_succeeded", runs=self._stats[collector.name]["runs"])
            except Exception as err:
                self._stats[collector.name]["failures"] += 1
                self._stats[collector.name]["last_error_at"] = datetime.now(timezone.utc).isoformat()
                self._stats[collector.name]["last_error"] = str(err)
                logging.exception("collector=%s event=run_failed failures=%s", collector.name, self._stats[collector.name]["failures"])
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
