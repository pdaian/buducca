from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from assistant_framework.ingestion import append_normalized_records, normalize_collected_item, write_raw_snapshot
from assistant_framework.workspace import Workspace

NAME = "reddit_top"
DESCRIPTION = "Collects each configured subreddit's public top 100 posts from the last 24 hours."
INTERVAL_SECONDS = 3600
CRAWL_INTERVAL = timedelta(hours=24)
RETRY_BACKOFF = timedelta(hours=6)
OUTPUT_DIR = "reddit"
STATUS_DIR = "collectors/reddit_top/status"
FILE_STRUCTURE = ["collectors/reddit_top/__init__.py", "collectors/reddit_top/README.md"]
GENERATED_FILES = [OUTPUT_DIR, STATUS_DIR, f"collected/normalized/{NAME}.jsonl"]
USER_AGENT = "Mozilla/5.0 (compatible; buducca-reddit-collector/1.0; +https://github.com/)"
REDDIT_TOP_ENDPOINTS = (
    "https://api.reddit.com/r/{subreddit}/top.json?{query}",
    "https://old.reddit.com/r/{subreddit}/top.json?{query}",
    "https://www.reddit.com/r/{subreddit}/top.json?{query}",
)
SUBREDDIT_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def register_collector(config: dict[str, Any]):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    retry_backoff = timedelta(seconds=float(config.get("retry_backoff_seconds", RETRY_BACKOFF.total_seconds())))
    timeout = float(config.get("timeout_seconds", 30))
    user_agent = str(config.get("user_agent") or USER_AGENT)
    subreddits = _normalize_subreddits(config.get("subreddits", []))

    def _run(workspace: Workspace) -> None:
        now = datetime.now(timezone.utc)
        normalized_records: list[dict[str, Any]] = []
        failures: list[str] = []
        successes = 0

        for subreddit in subreddits:
            if not _is_due(workspace, subreddit, now, retry_backoff=retry_backoff):
                continue
            status = _load_status(workspace, subreddit)
            status.update({"subreddit": subreddit, "last_attempt_at": now.isoformat()})
            try:
                posts = _fetch_top_posts(subreddit, timeout_seconds=timeout, user_agent=user_agent)
                write_raw_snapshot(workspace, f"{NAME}_{subreddit}", posts)

                records = []
                for post in posts:
                    record = {
                        "source": "reddit_top",
                        "collector": NAME,
                        "subreddit": subreddit,
                        "collected_at": now.isoformat(),
                        **post,
                    }
                    records.append(record)
                    normalized_records.append(
                        normalize_collected_item(
                            source="reddit_top",
                            timestamp=now.isoformat(),
                            title=str(post.get("title") or post.get("id") or subreddit),
                            text=str(post.get("selftext") or ""),
                            metadata={"subreddit": subreddit, **post},
                        )
                    )

                workspace.write_text(
                    _output_path(subreddit),
                    "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + ("\n" if records else ""),
                )
                status.update(
                    {
                        "last_success_at": now.isoformat(),
                        "last_error": None,
                        "last_error_at": None,
                        "last_item_count": len(records),
                    }
                )
                successes += 1
            except Exception as exc:
                status.update(
                    {
                        "last_error": str(exc),
                        "last_error_at": now.isoformat(),
                    }
                )
                failures.append(f"{subreddit}: {exc}")
            workspace.write_text(_status_path(subreddit), json.dumps(status, indent=2, sort_keys=True) + "\n")

        append_normalized_records(workspace, NAME, normalized_records)
        if failures and successes == 0:
            raise RuntimeError("; ".join(failures))

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "interval_seconds": interval,
        "generated_files": GENERATED_FILES,
        "file_structure": FILE_STRUCTURE,
        "run": _run,
    }


def _normalize_subreddits(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    if not isinstance(raw_value, list):
        raise ValueError("subreddits must be a list")

    seen: set[str] = set()
    subreddits: list[str] = []
    for item in raw_value:
        subreddit = str(item).strip()
        if subreddit.lower().startswith("r/"):
            subreddit = subreddit[2:]
        subreddit = subreddit.lower()
        if not subreddit:
            continue
        if not SUBREDDIT_PATTERN.fullmatch(subreddit):
            raise ValueError(f"invalid subreddit: {item}")
        if subreddit in seen:
            continue
        seen.add(subreddit)
        subreddits.append(subreddit)
    return subreddits


def _status_path(subreddit: str) -> str:
    return f"{STATUS_DIR}/{subreddit}.json"


def _output_path(subreddit: str) -> str:
    return f"{OUTPUT_DIR}/{subreddit}.top.day.jsonl"


def _is_due(workspace: Workspace, subreddit: str, now: datetime, *, retry_backoff: timedelta) -> bool:
    status = _load_status(workspace, subreddit)
    last_success_at = status.get("last_success_at")
    if not last_success_at:
        last_attempt_at = status.get("last_attempt_at")
        if not last_attempt_at:
            return True
        try:
            last_attempt = datetime.fromisoformat(last_attempt_at)
        except Exception:
            return True
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=timezone.utc)
        return now - last_attempt >= retry_backoff
    try:
        last_success = datetime.fromisoformat(last_success_at)
    except Exception:
        return True

    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    return now - last_success >= CRAWL_INTERVAL


def _load_status(workspace: Workspace, subreddit: str) -> dict[str, Any]:
    raw = workspace.read_text(_status_path(subreddit), default="")
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _fetch_top_posts(subreddit: str, *, timeout_seconds: float, user_agent: str) -> list[dict[str, Any]]:
    query = urlencode({"t": "day", "limit": 100, "raw_json": 1})
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last_error: Exception | None = None
    payload: dict[str, Any] | None = None

    for endpoint in REDDIT_TOP_ENDPOINTS:
        request = Request(endpoint.format(subreddit=subreddit, query=query), headers=headers)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as exc:
            last_error = exc
            if exc.code != 403:
                raise

    if payload is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"failed to fetch subreddit: {subreddit}")

    children = payload.get("data", {}).get("children", [])
    posts: list[dict[str, Any]] = []
    for child in children:
        data = child.get("data", {})
        posts.append(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "title": data.get("title", ""),
                "author": data.get("author", ""),
                "selftext": data.get("selftext", ""),
                "url": data.get("url", ""),
                "permalink": f"https://www.reddit.com{data.get('permalink', '')}",
                "created_utc": data.get("created_utc"),
                "score": data.get("score"),
                "upvote_ratio": data.get("upvote_ratio"),
                "num_comments": data.get("num_comments"),
                "over_18": data.get("over_18", False),
            }
        )
    return posts
