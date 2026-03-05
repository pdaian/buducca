import json
import tempfile
import unittest

from assistant_framework.workspace import Workspace
from collectors.google_calendar import create_collector as create_calendar_collector
from collectors.twitter_recent import create_collector as create_twitter_collector


class _FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs

    def __call__(self, command, timeout_seconds=60.0, cwd=None):
        return self.outputs.pop(0)


class NewCollectorsTests(unittest.TestCase):
    def test_twitter_writes_following_and_dms_separately(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            collector = create_twitter_collector(
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

    def test_google_calendar_creates_account_month_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            collector = create_calendar_collector(
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


if __name__ == "__main__":
    unittest.main()
