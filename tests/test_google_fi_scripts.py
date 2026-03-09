import json
import subprocess
import unittest


class GoogleFiScriptsTests(unittest.TestCase):
    def test_receive_dry_run(self) -> None:
        proc = subprocess.run(
            ["python3", "-m", "messaging_llm_bot.google_fi_client", "receive", "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {"messages": [], "calls": []})

    def test_send_dry_run(self) -> None:
        proc = subprocess.run(
            ["python3", "-m", "messaging_llm_bot.google_fi_client", "send", "--dry-run", "--recipient", "thread-1", "--message", "hi"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(json.loads(proc.stdout)["ok"])

    def test_list_messages_dry_run(self) -> None:
        proc = subprocess.run(
            ["python3", "-m", "messaging_llm_bot.google_fi_client", "list-messages", "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
