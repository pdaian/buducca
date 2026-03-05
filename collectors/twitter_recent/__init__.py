from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from assistant_framework.collector_shell import run_command
from assistant_framework.workspace import Workspace

NAME = "twitter_recent"
INTERVAL_SECONDS = 180
STATE_FILE = "collectors/twitter_recent.state.json"
FOLLOWING_OUTPUT_FILE = "twitter.following.recent"
DMS_OUTPUT_FILE = "twitter.dms.recent"


def create_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 90))
    following_command = config.get("following_command") or os.environ.get("TWITTER_FOLLOWING_COMMAND", "")
    dms_command = config.get("dms_command") or os.environ.get("TWITTER_DMS_COMMAND", "")

    def _run(workspace: Workspace) -> None:
        state = json.loads(
            workspace.read_text(STATE_FILE, default='{"following_last_id": "0", "dm_last_id": "0"}')
        )
        following_last = str(state.get("following_last_id", "0"))
        dm_last = str(state.get("dm_last_id", "0"))
        now = datetime.now(timezone.utc).isoformat()

        if following_command:
            code, stdout, _ = run_command(following_command, timeout_seconds=timeout)
            if code == 0 and stdout.strip():
                posts = json.loads(stdout)
                posts = posts if isinstance(posts, list) else posts.get("posts", [])
                out = []
                max_id = following_last
                for post in posts:
                    post_id = str(post.get("id", "0"))
                    if post_id <= following_last:
                        continue
                    max_id = max(max_id, post_id)
                    out.append({"source": "twitter_following", "received_at": now, **post})
                if out:
                    workspace.write_text(
                        FOLLOWING_OUTPUT_FILE, "\n".join(json.dumps(item, ensure_ascii=False) for item in out) + "\n"
                    )
                state["following_last_id"] = max_id

        if dms_command:
            code, stdout, _ = run_command(dms_command, timeout_seconds=timeout)
            if code == 0 and stdout.strip():
                dms = json.loads(stdout)
                dms = dms if isinstance(dms, list) else dms.get("messages", [])
                out = []
                max_id = dm_last
                for dm in dms:
                    dm_id = str(dm.get("id", "0"))
                    if dm_id <= dm_last:
                        continue
                    max_id = max(max_id, dm_id)
                    out.append({"source": "twitter_dm", "received_at": now, **dm})
                if out:
                    workspace.write_text(
                        DMS_OUTPUT_FILE, "\n".join(json.dumps(item, ensure_ascii=False) for item in out) + "\n"
                    )
                state["dm_last_id"] = max_id

        workspace.write_text(STATE_FILE, json.dumps(state))

    return {"name": NAME, "interval_seconds": interval, "run": _run}
