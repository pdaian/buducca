import unittest

from assistant_framework.cli import build_parser
from assistant_framework.traces import write_trace
from assistant_framework.workspace import Workspace


class CLITests(unittest.TestCase):
    def test_compressors_command_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["compressors"])
        self.assertEqual(args.workspace, "workspace")
        self.assertEqual(args.compressors, "compressors")
        self.assertEqual(args.config, "compressors/config.json")

    def test_trace_command_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["trace", "last-prompt"])
        self.assertEqual(args.workspace, "workspace")

    def test_trace_replay_returns_final_reply(self) -> None:
        with self.subTest("replay"):
            import tempfile
            from io import StringIO
            from contextlib import redirect_stdout

            with tempfile.TemporaryDirectory() as td:
                workspace = Workspace(td)
                write_trace(workspace, {"final_reply": "done"})
                parser = build_parser()
                args = parser.parse_args(["trace", "replay", "--workspace", td])
                out = StringIO()
                with redirect_stdout(out):
                    args.handler(args)
                self.assertEqual(out.getvalue().strip(), "done")


if __name__ == "__main__":
    unittest.main()
