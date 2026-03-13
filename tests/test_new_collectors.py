import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from assistant_framework.workspace import Workspace
from collectors.google_calendar import register_collector as register_calendar_collector
from collectors.reddit_top import register_collector as register_reddit_collector
from collectors.twitter_recent import register_collector as register_twitter_collector
from collectors.gmail import register_collector as register_gmail_collector


class _FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs

    def __call__(self, command, timeout_seconds=60.0, cwd=None):
        return self.outputs.pop(0)


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class NewCollectorsTests(unittest.TestCase):
    def test_twitter_writes_following_and_dms_separately(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            collector = register_twitter_collector(
                {
                    "following_command": "following",
                    "dms_command": "dms",
                }
            )

            fake = _FakeRunner(
                [
                    (0, json.dumps([{"id": "11", "text": "post"}]), ""),
                    (0, json.dumps([{"id": "4", "text": "dm"}]), ""),
                ]
            )

            from unittest.mock import patch

            with patch("collectors.twitter_recent.run_command", fake):
                collector["run"](workspace)

            self.assertIn('"post"', workspace.read_text("twitter.following.recent"))
            self.assertIn('"dm"', workspace.read_text("twitter.dms.recent"))
            self.assertIn('"source": "twitter_following"', workspace.read_text("collected/normalized/twitter_recent.jsonl"))

    def test_google_calendar_creates_account_month_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            collector = register_calendar_collector(
                {
                    "accounts": ["a@example.com"],
                    "command_template": "echo calendar",
                }
            )

            fake = _FakeRunner([(0, json.dumps([{"id": "evt-1", "summary": "Standup"}]), "")])

            from unittest.mock import patch

            with patch("collectors.google_calendar.run_command", fake):
                collector["run"](workspace)

            files = list(workspace.resolve("google_calendar").glob("*.events.jsonl"))
            self.assertEqual(len(files), 1)
            self.assertIn("Standup", files[0].read_text(encoding="utf-8"))
            self.assertIn("Standup", workspace.read_text("collected/normalized/google_calendar.jsonl"))

    def test_gmail_supports_multiple_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            collector = register_gmail_collector(
                {
                    "accounts": [
                        {"name": "personal", "command": "gmail-a"},
                        {"name": "work", "command": "gmail-b"},
                    ]
                }
            )

            fake = _FakeRunner(
                [
                    (0, json.dumps([{"id": "1", "subject": "a"}]), ""),
                    (0, json.dumps([{"id": "2", "subject": "b"}]), ""),
                ]
            )

            from unittest.mock import patch

            with patch("collectors.gmail.run_command", fake):
                collector["run"](workspace)

            output = workspace.read_text("gmail.recent")
            self.assertIn('"account": "personal"', output)
            self.assertIn('"account": "work"', output)

    def test_reddit_fetches_due_subreddits_and_writes_status_per_subreddit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            workspace.write_text(
                "collectors/reddit_top/status/python.json",
                json.dumps({"subreddit": "python", "last_success_at": recent}),
            )

            collector = register_reddit_collector({"subreddits": ["python", "LocalLLaMA"]})
            payload = {
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "abc123",
                                "name": "t3_abc123",
                                "title": "Test post",
                                "author": "alice",
                                "selftext": "Collected from Reddit",
                                "url": "https://example.com/post",
                                "permalink": "/r/LocalLLaMA/comments/abc123/test_post/",
                                "created_utc": 1700000000,
                                "score": 42,
                                "upvote_ratio": 0.98,
                                "num_comments": 7,
                                "over_18": False,
                            }
                        }
                    ]
                }
            }

            from unittest.mock import patch

            with patch("collectors.reddit_top.urlopen", return_value=_FakeResponse(payload)) as mocked:
                collector["run"](workspace)

            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(workspace.read_text("reddit/python.top.day.jsonl"), "")
            output = workspace.read_text("reddit/localllama.top.day.jsonl")
            self.assertIn('"subreddit": "localllama"', output)
            self.assertIn('"title": "Test post"', output)

            status = json.loads(workspace.read_text("collectors/reddit_top/status/localllama.json"))
            self.assertEqual(status["subreddit"], "localllama")
            self.assertEqual(status["last_item_count"], 1)
            self.assertIsNotNone(status["last_success_at"])
            self.assertIsNone(status["last_error"])

            normalized = workspace.read_text("collected/normalized/reddit_top.jsonl")
            self.assertIn('"source": "reddit_top"', normalized)
            self.assertIn('"subreddit": "localllama"', normalized)


if __name__ == "__main__":
    unittest.main()
