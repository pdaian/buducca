from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any

from assistant_framework.workspace import Workspace

NAME = "openhue"
DESCRIPTION = (
    "Control OpenHue lights by id or name. "
    "Use args.action with one of: list/on/off/toggle. "
    "For on/off/toggle pass args.lights as a list of ids and/or names. "
    "Optional args: brightness (1-254), transition_ms."
)


def _run_shell(command: str, timeout: float = 20.0) -> tuple[int, str, str]:
    result = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=timeout, check=False)
    return result.returncode, result.stdout or "", result.stderr or ""


def _parse_lights_payload(stdout: str) -> list[dict[str, Any]]:
    parsed = json.loads(stdout)
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        lights = parsed.get("lights", [])
        if isinstance(lights, list):
            return [item for item in lights if isinstance(item, dict)]
    return []


def _normalize_lookup(lights: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for light in lights:
        light_id = str(light.get("id") or light.get("light_id") or "").strip()
        if not light_id:
            continue
        light_name = str(light.get("name") or light.get("label") or "").strip()
        by_id[light_id] = light_name or light_id
        if light_name:
            by_name[light_name.lower()] = light_id
    return by_id, by_name


def _resolve_targets(targets: list[str], by_id: dict[str, str], by_name: dict[str, str]) -> tuple[list[tuple[str, str]], list[str]]:
    resolved: list[tuple[str, str]] = []
    missing: list[str] = []
    seen: set[str] = set()
    for target in targets:
        raw = str(target).strip()
        if not raw:
            continue
        target_id = raw if raw in by_id else by_name.get(raw.lower())
        if not target_id:
            missing.append(raw)
            continue
        if target_id in seen:
            continue
        seen.add(target_id)
        resolved.append((target_id, by_id.get(target_id, target_id)))
    return resolved, missing


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    del workspace

    action = str(args.get("action") or args.get("command") or "list").strip().lower()
    timeout = float(args.get("timeout_seconds", 20))

    list_command = str(
        args.get("list_command")
        or os.environ.get("OPENHUE_LIST_COMMAND")
        or "openhue lights list --format json"
    )

    if action == "list":
        code, stdout, stderr = _run_shell(list_command, timeout=timeout)
        if code != 0:
            return f"OpenHue list command failed: {(stderr or stdout).strip() or f'exit {code}'}"
        try:
            lights = _parse_lights_payload(stdout)
        except json.JSONDecodeError:
            return "OpenHue list command returned invalid JSON."
        if not lights:
            return "No lights returned by OpenHue."
        lines = ["Available OpenHue lights:"]
        for light in lights:
            light_id = str(light.get("id") or light.get("light_id") or "?")
            light_name = str(light.get("name") or light.get("label") or "unnamed")
            lines.append(f"- {light_name} (id: {light_id})")
        return "\n".join(lines)

    if action not in {"on", "off", "toggle"}:
        return "Unsupported action. Use one of: list, on, off, toggle."

    raw_lights = args.get("lights")
    if not isinstance(raw_lights, list) or not raw_lights:
        return "Missing required arg `lights` (non-empty list of names and/or ids)."

    code, stdout, stderr = _run_shell(list_command, timeout=timeout)
    if code != 0:
        return f"OpenHue list command failed: {(stderr or stdout).strip() or f'exit {code}'}"

    try:
        lights = _parse_lights_payload(stdout)
    except json.JSONDecodeError:
        return "OpenHue list command returned invalid JSON."

    by_id, by_name = _normalize_lookup(lights)
    targets, missing = _resolve_targets([str(item) for item in raw_lights], by_id, by_name)
    if not targets:
        return "No matching lights found for the provided `lights` values."

    brightness = args.get("brightness")
    transition_ms = args.get("transition_ms")
    template = str(
        args.get("set_command_template")
        or os.environ.get("OPENHUE_SET_COMMAND_TEMPLATE")
        or "openhue lights {action} --id {id}"
    )

    errors: list[str] = []
    changed: list[str] = []
    for target_id, target_name in targets:
        command = template.format(action=action, id=target_id, name=shlex.quote(target_name))
        if brightness is not None:
            command += f" --brightness {int(brightness)}"
        if transition_ms is not None:
            command += f" --transition-ms {int(transition_ms)}"

        c, out, err = _run_shell(command, timeout=timeout)
        if c != 0:
            errors.append(f"{target_name} ({target_id}): {(err or out).strip() or f'exit {c}'}")
            continue
        changed.append(f"{target_name} ({target_id})")

    response: list[str] = []
    if changed:
        response.append(f"Applied `{action}` to: " + ", ".join(changed))
    if missing:
        response.append("Not found: " + ", ".join(missing))
    if errors:
        response.append("Failed: " + " | ".join(errors))
    return "\n".join(response) if response else "No changes applied."
