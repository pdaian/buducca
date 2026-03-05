from __future__ import annotations

import argparse
import json
from pathlib import Path

from assistant_framework.telegram_user_client import TelegramUserClient


def load_collector_config(path: str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    collectors = raw.get("collectors", {})
    return collectors.get("telegram_recent") or collectors.get("telegram_recent_collector") or {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Complete Telegram user-client signup for telegram_recent collector")
    parser.add_argument("--config", default="agent_config.json", help="Collector config JSON path")
    args = parser.parse_args()

    cfg = load_collector_config(args.config)
    user_cfg = cfg.get("user_client") or {}
    if not user_cfg.get("enabled", False):
        raise SystemExit("collectors.telegram_recent.user_client.enabled must be true")

    client = TelegramUserClient(
        api_id=int(user_cfg.get("api_id", 0)),
        api_hash=str(user_cfg.get("api_hash", "")),
        session_path=str(user_cfg.get("session_path", "workspace/collectors/telegram_user")),
        phone=str(user_cfg.get("phone", "")) or None,
        request_timeout_seconds=float(cfg.get("timeout_seconds", 30)),
        qr_wait_seconds=int(user_cfg.get("qr_wait_seconds", 120)),
    )
    client.signup()
    print("telegram_recent user-client signup completed")


if __name__ == "__main__":
    main()
