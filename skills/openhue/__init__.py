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


def _coerce_command(value: Any, *, field_name: str) -> list[str]:
    if isinstance(value, list):
        command = []
        for item in value:
            rendered = str(item).strip()
            if rendered:
                command.append(rendered)
    else:
        command = shlex.split(str(value))
    if not command:
        raise ValueError(f"`{field_name}` must resolve to a non-empty command.")
    return command


def _format_command_template(template: Any, *, action: str, light_id: str, light_name: str) -> list[str]:
    raw_values = {"action": action, "id": light_id, "name": light_name}
    if isinstance(template, list):
        return _coerce_command(
            [str(item).format(**raw_values) for item in template],
            field_name="set_command_template",
        )

    quoted_values = {key: shlex.quote(value) for key, value in raw_values.items()}
    return _coerce_command(
        str(template).format(**quoted_values),
        field_name="set_command_template",
    )


def _run_command(command: list[str], timeout: float = 20.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return 124, _process_output_text(exc.stdout), f"Command timed out after {timeout:g}s."
    except OSError as exc:
        return 127, "", str(exc)
    return result.returncode, result.stdout or "", result.stderr or ""


def _process_output_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _resolve_int_option(args: dict[str, Any], field_name: str, *, minimum: int, maximum: int | None = None) -> int | None:
    value = args.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"`{field_name}` must be an integer.")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"`{field_name}` must be an integer.") from exc
    if resolved < minimum or (maximum is not None and resolved > maximum):
        range_text = f"{minimum}-{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"`{field_name}` must be in range {range_text}.")
    return resolved


def _load_lights(list_command: list[str], *, timeout: float) -> tuple[str | None, list[dict[str, Any]]]:
    code, stdout, stderr = _run_command(list_command, timeout=timeout)
    if code != 0:
        return f"OpenHue list command failed: {(stderr or stdout).strip() or f'exit {code}'}", []
    try:
        return None, _parse_lights_payload(stdout)
    except json.JSONDecodeError:
        return "OpenHue list command returned invalid JSON.", []


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
    try:
        timeout = float(args.get("timeout_seconds", 20))
    except (TypeError, ValueError):
        return "`timeout_seconds` must be a number."
    if timeout <= 0:
        return "`timeout_seconds` must be greater than 0."

    list_command_value = (
        args.get("list_command")
        or os.environ.get("OPENHUE_LIST_COMMAND")
        or "openhue lights list --format json"
    )
    try:
        list_command_args = _coerce_command(list_command_value, field_name="list_command")
    except ValueError as exc:
        return str(exc)

    if action == "list":
        error, lights = _load_lights(list_command_args, timeout=timeout)
        if error:
            return error
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

    error, lights = _load_lights(list_command_args, timeout=timeout)
    if error:
        return error

    by_id, by_name = _normalize_lookup(lights)
    targets, missing = _resolve_targets([str(item) for item in raw_lights], by_id, by_name)
    if not targets:
        return "No matching lights found for the provided `lights` values."

    try:
        brightness = _resolve_int_option(args, "brightness", minimum=1, maximum=254)
        transition_ms = _resolve_int_option(args, "transition_ms", minimum=0)
    except ValueError as exc:
        return str(exc)

    template = (
        args.get("set_command_template")
        or os.environ.get("OPENHUE_SET_COMMAND_TEMPLATE")
        or "openhue lights {action} --id {id}"
    )

    errors: list[str] = []
    changed: list[str] = []
    for target_id, target_name in targets:
        try:
            command = _format_command_template(template, action=action, light_id=target_id, light_name=target_name)
        except (KeyError, ValueError) as exc:
            return f"Invalid OpenHue set command template: {exc}"
        if brightness is not None:
            command.extend(["--brightness", str(brightness)])
        if transition_ms is not None:
            command.extend(["--transition-ms", str(transition_ms)])

        c, out, err = _run_command(command, timeout=timeout)
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


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
    }
