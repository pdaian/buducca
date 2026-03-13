# Reddit top collector

Collects the public top 100 posts from the last 24 hours for each configured subreddit.

It uses Reddit's public JSON endpoint with the Python standard library. No account, OAuth flow, or third-party package is required.

## Setup
1. Add `collectors.reddit_top.subreddits` in your collector config.
2. Optionally set `timeout_seconds`, `interval_seconds`, or `user_agent`.
3. Run `python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config config/collectors`.

Example:
```json
{
  "subreddits": ["python", "machinelearning", "localllama"]
}
```

Generated workspace files:
- `workspace/reddit/<subreddit>.top.day.jsonl`
- `workspace/collectors/reddit_top/status/<subreddit>.json`
- `workspace/collected/normalized/reddit_top.jsonl`

Each subreddit keeps its own status file. A subreddit is only fetched again after 24 hours have passed since its last successful crawl.

## File structure
- `collectors/reddit_top/__init__.py`
- `collectors/reddit_top/README.md`
