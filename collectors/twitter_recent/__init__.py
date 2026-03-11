from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from assistant_framework.collector_shell import run_command
from assistant_framework.ingestion import append_normalized_records, normalize_collected_item, write_raw_snapshot
from assistant_framework.workspace import Workspace

NAME = "twitter_recent"
DESCRIPTION = "Collects recent Twitter following posts and direct messages into separate workspace files."
INTERVAL_SECONDS = 180
STATE_FILE = "collectors/twitter_recent.state.json"
FOLLOWING_OUTPUT_FILE = "twitter.following.recent"
DMS_OUTPUT_FILE = "twitter.dms.recent"
FILE_STRUCTURE = ["collectors/twitter_recent/__init__.py", "collectors/twitter_recent/README.md"]
GENERATED_FILES = [FOLLOWING_OUTPUT_FILE, DMS_OUTPUT_FILE, STATE_FILE]


def register_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 90))
    default_following = config.get("following_command") or os.environ.get("TWITTER_FOLLOWING_COMMAND", "")
    default_dms = config.get("dms_command") or os.environ.get("TWITTER_DMS_COMMAND", "")
    accounts = config.get("accounts") or [
        {
            "name": config.get("account_name", "default"),
            "following_command": default_following,
            "dms_command": default_dms,
        }
    ]

    def _run(workspace: Workspace) -> None:
        state = json.loads(workspace.read_text(STATE_FILE, default='{"accounts": {}}'))
        account_state = state.setdefault("accounts", {})
        now = datetime.now(timezone.utc).isoformat()
        following_out = []
        dm_out = []
        normalized_records = []

        for account in accounts:
            account_name = str(account.get("name") or "default")
            acc_state = account_state.setdefault(account_name, {"following_last_id": "0", "dm_last_id": "0"})
            following_last = str(acc_state.get("following_last_id", "0"))
            dm_last = str(acc_state.get("dm_last_id", "0"))

            following_command = account.get("following_command") or default_following
            if following_command:
                code, stdout, _ = run_command(following_command, timeout_seconds=timeout)
                if code == 0 and stdout.strip():
                    posts = json.loads(stdout)
                    write_raw_snapshot(workspace, f"{NAME}_following", posts)
                    posts = posts if isinstance(posts, list) else posts.get("posts", [])
                    max_id = following_last
                    for post in posts:
                        post_id = str(post.get("id", "0"))
                        if post_id <= following_last:
                            continue
                        max_id = max(max_id, post_id)
                        following_out.append(
                            {"source": "twitter_following", "collector": NAME, "account": account_name, "collected_at": now, **post}
                        )
                        normalized_records.append(
                            normalize_collected_item(
                                source="twitter_following",
                                timestamp=now,
                                title=str(post.get("author") or post_id),
                                text=str(post.get("text") or ""),
                                metadata={"account": account_name, **post},
                            )
                        )
                    acc_state["following_last_id"] = max_id

            dms_command = account.get("dms_command") or default_dms
            if dms_command:
                code, stdout, _ = run_command(dms_command, timeout_seconds=timeout)
                if code == 0 and stdout.strip():
                    dms = json.loads(stdout)
                    write_raw_snapshot(workspace, f"{NAME}_dms", dms)
                    dms = dms if isinstance(dms, list) else dms.get("messages", [])
                    max_id = dm_last
                    for dm in dms:
                        dm_id = str(dm.get("id", "0"))
                        if dm_id <= dm_last:
                            continue
                        max_id = max(max_id, dm_id)
                        dm_out.append(
                            {"source": "twitter_dm", "collector": NAME, "account": account_name, "collected_at": now, **dm}
                        )
                        normalized_records.append(
                            normalize_collected_item(
                                source="twitter_dm",
                                timestamp=now,
                                title=str(dm.get("sender") or dm_id),
                                text=str(dm.get("text") or ""),
                                metadata={"account": account_name, **dm},
                            )
                        )
                    acc_state["dm_last_id"] = max_id

        if following_out:
            workspace.write_text(
                FOLLOWING_OUTPUT_FILE,
                "\n".join(json.dumps(item, ensure_ascii=False) for item in following_out) + "\n",
            )
        if dm_out:
            workspace.write_text(DMS_OUTPUT_FILE, "\n".join(json.dumps(item, ensure_ascii=False) for item in dm_out) + "\n")
        append_normalized_records(workspace, NAME, normalized_records)
        workspace.write_text(STATE_FILE, json.dumps(state))

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "interval_seconds": interval,
        "generated_files": GENERATED_FILES,
        "file_structure": FILE_STRUCTURE,
        "run": _run,
    }
