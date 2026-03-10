import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from assistant_framework.compressors import Compressor, CompressorManager, CompressorRunner
from assistant_framework.workspace import Workspace
from compressors.file_size import create_compressor as create_file_size_compressor
from compressors.llm_based import create_compressor as create_llm_compressor


class CompressorRunnerTests(unittest.TestCase):
    def test_runner_writes_status_with_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)

            def _run(workspace: Workspace) -> None:
                workspace.write_text("compressed.out", "ok")

            runner = CompressorRunner(ws, [Compressor(name="demo", interval_seconds=60, run=_run)])
            next_run = {"demo": 0.0}
            runner.run_once(next_run, now=100.0)

            raw = ws.read_text("compressor_status.json")
            status = json.loads(raw)
            self.assertEqual(status["compressor_count"], 1)
            self.assertEqual(status["compressors"]["demo"]["runs"], 1)
            self.assertIsNotNone(status["compressors"]["demo"]["last_success_at"])


class FileSizeCompressorTests(unittest.TestCase):
    def test_file_size_trims_to_last_200_lines_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)
            ws.write_text("notes.txt", "".join(f"line-{i}\n" for i in range(250)))

            compressor = create_file_size_compressor({})
            compressor["run"](ws)

            lines = ws.read_text("notes.txt").splitlines()
            self.assertEqual(len(lines), 200)
            self.assertEqual(lines[0], "line-50")
            self.assertEqual(lines[-1], "line-249")


class LLMCompressorTests(unittest.TestCase):
    def test_llm_compressor_backs_up_once_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)
            ws.write_text("learnings", "A\nA\nB\n")
            compressor = create_llm_compressor(
                {
                    "command": "python3 scripts/memory_compressor.py",
                    "files": [
                        {
                            "path": "learnings",
                            "backup_path": "learnings.back",
                            "prompt": "compress {file_path} at {now}",
                            "interval_seconds": 0,
                        }
                    ],
                }
            )

            compressor["run"](ws)
            first_backup = ws.read_text("learnings.back")
            self.assertEqual(first_backup, "A\nA\nB\n")

            ws.write_text("learnings", "C\nC\n")
            compressor["run"](ws)
            second_backup = ws.read_text("learnings.back")
            self.assertEqual(second_backup, "A\nA\nB\n")


class CompressorLoadingTests(unittest.TestCase):
    def test_compressor_manager_loads_create_compressor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            compressors_dir = Path(td) / "compressors"
            compressors_dir.mkdir()
            (compressors_dir / "demo_compressor.py").write_text(
                textwrap.dedent(
                    """
                    def create_compressor(config):
                        def run(workspace):
                            workspace.write_text("compressor.txt", config.get("value", "none"))
                        return {"name": "demo", "interval_seconds": 1, "run": run}
                    """
                ),
                encoding="utf-8",
            )

            manager = CompressorManager(compressors_dir, config={"demo_compressor": {"value": "fresh"}})
            compressors = manager.load()

            ws = Workspace(Path(td) / "workspace")
            compressors[0].run(ws)
            self.assertEqual(ws.read_text("compressor.txt"), "fresh")


if __name__ == "__main__":
    unittest.main()
