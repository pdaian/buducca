from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_DEVICE_NAME = "buducca"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_QR_OUTPUT = "workspace/signal_frontend_qr.txt"


def _coerce_output(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return ""


def _load_json(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def _build_signup_settings(config: dict[str, Any]) -> tuple[list[str], float, str]:
    signal_cfg = config.get("signal") if isinstance(config.get("signal"), dict) else {}

    device_name = str(signal_cfg.get("device_name") or DEFAULT_DEVICE_NAME)
    raw_timeout = signal_cfg.get("signup_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    timeout_seconds = float(raw_timeout)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    qr_output = str(signal_cfg.get("qr_output") or DEFAULT_QR_OUTPUT)

    link_command = signal_cfg.get("link_command")
    if not isinstance(link_command, list) or not link_command:
        link_command = ["signal-cli", "link", "-n", device_name]
    return [str(part) for part in link_command], timeout_seconds, qr_output


def run_signup(config_path: str) -> int:
    config = _load_json(config_path)
    link_command, timeout_seconds, qr_output = _build_signup_settings(config)

    print(f"Running Signal signup command: {' '.join(link_command)}")
    print(f"Waiting up to {int(timeout_seconds)} seconds for provisioning output...")

    try:
        proc = subprocess.run(
            link_command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        output = proc.stdout or proc.stderr or ""
        return_code = int(proc.returncode)
    except subprocess.TimeoutExpired as exc:
        stdout = _coerce_output(exc.stdout)
        stderr = _coerce_output(exc.stderr)
        output = stdout or stderr or ""
        return_code = 124
        print(
            "Signal signup timed out before command completed. "
            "If a provisioning link was emitted, it was still captured to the output file."
        )

    qr_file = Path(qr_output)
    qr_file.parent.mkdir(parents=True, exist_ok=True)
    qr_file.write_text(output, encoding="utf-8")

    print(f"Signal QR output saved to: {qr_output}")
    print("Open the file and scan the QR/provisioning link with Signal > Linked Devices.")
    return return_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Signal frontend QR signup flow")
    parser.add_argument("--config", default="config.json", help="Bot config JSON file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(run_signup(args.config))


if __name__ == "__main__":
    main()
