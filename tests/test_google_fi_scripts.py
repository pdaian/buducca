import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class GoogleFiScriptsTests(unittest.TestCase):
    def test_receive_reads_and_truncates_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            inbox = workspace / "google_fi_inbox.json"
            inbox.write_text(
                json.dumps(
                    {
                        "messages": [{"conversation_id": "t1", "sender_id": "+1", "text": "hello"}],
                        "calls": [{"conversation_id": "t1", "sender_id": "+1", "status": "missed"}],
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                ["python3", "scripts/google_fi_receive.py", "--workspace", str(workspace)],
                capture_output=True,
                text=True,
                check=False,
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertEqual(len(payload["messages"]), 1)
            self.assertEqual(len(payload["calls"]), 1)

            truncated = json.loads(inbox.read_text(encoding="utf-8"))
            self.assertEqual(truncated, {"messages": [], "calls": []})

    def test_send_appends_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            proc = subprocess.run(
                [
                    "python3",
                    "scripts/google_fi_send.py",
                    "--workspace",
                    str(workspace),
                    "--recipient",
                    "thread-1",
                    "--message",
                    "hi",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertEqual(proc.returncode, 0)
            outbox = workspace / "google_fi_outbox.jsonl"
            lines = outbox.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            event = json.loads(lines[0])
            self.assertEqual(event["recipient"], "thread-1")
            self.assertEqual(event["message"], "hi")


if __name__ == "__main__":
    unittest.main()
