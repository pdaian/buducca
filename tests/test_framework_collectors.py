import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.collectors import Collector, CollectorRunner
from assistant_framework.workspace import Workspace


class CollectorRunnerTests(unittest.TestCase):
    def test_runner_writes_status_with_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)

            def _run(workspace: Workspace) -> None:
                workspace.write_text("collector.out", "ok")

            runner = CollectorRunner(
                ws,
                [
                    Collector(
                        name="demo",
                        description="Demo collector",
                        interval_seconds=60,
                        run=_run,
                        generated_files=["collector.out"],
                        module_files=["collectors/demo.py"],
                    )
                ],
            )
            next_run = {"demo": 0.0}
            runner.run_once(next_run, now=100.0)

            raw = ws.read_text("collector_status.json")
            status = json.loads(raw)
            self.assertEqual(status["collector_count"], 1)
            self.assertEqual(status["collectors"]["demo"]["runs"], 1)
            self.assertIsNotNone(status["collectors"]["demo"]["last_success_at"])
            self.assertEqual(status["collectors"]["demo"]["generated_files"], ["collector.out"])
            self.assertEqual(status["collectors"]["demo"]["description"], "Demo collector")


if __name__ == "__main__":
    unittest.main()
