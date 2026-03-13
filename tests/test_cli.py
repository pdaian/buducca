import unittest

from assistant_framework.cli import build_parser
from assistant_framework.traces import write_trace
from assistant_framework.workspace import Workspace


class CLITests(unittest.TestCase):
    def test_collectors_command_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["collectors"])
        self.assertEqual(args.workspace, "workspace")
        self.assertEqual(args.collectors, "collectors")
        self.assertEqual(args.config, "config/collectors")

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

    def test_skills_list_returns_loaded_skill_metadata(self) -> None:
        import json
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir()
            (skills_dir / "demo.py").write_text(
                "def run(workspace, args):\n    return 'ok'\n"
                "def register():\n"
                "    return {'name': 'demo', 'description': 'Demo skill', 'run': run}\n",
                encoding="utf-8",
            )

            parser = build_parser()
            args = parser.parse_args(["skills", "list", "--skills", str(skills_dir)])
            out = StringIO()
            with redirect_stdout(out):
                args.handler(args)

            payload = json.loads(out.getvalue())
            self.assertEqual(payload, [{"name": "demo", "description": "Demo skill", "requires_llm_response": False}])

    def test_skills_inspect_returns_prompt_and_help_surfaces(self) -> None:
        import json
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            package = skills_dir / "demo_skill"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                "def run(workspace, args):\n    return 'ok'\n"
                "def register():\n"
                "    return {'name': 'demo_skill', 'description': 'Demo skill', 'run': run, 'args_schema': '{ value?: string }'}\n",
                encoding="utf-8",
            )
            (package / "README.md").write_text(
                "# Demo\n\n## What it does\nExplains the skill.\n",
                encoding="utf-8",
            )

            parser = build_parser()
            args = parser.parse_args(["skills", "inspect", "demo_skill", "--skills", str(skills_dir)])
            out = StringIO()
            with redirect_stdout(out):
                args.handler(args)

            payload = json.loads(out.getvalue())
            self.assertEqual(payload["name"], "demo_skill")
            self.assertEqual(payload["prompt_surface"]["description"], "Demo skill")
            self.assertEqual(payload["prompt_surface"]["args_schema"], "{ value?: string }")
            self.assertEqual(
                payload["prompt_surface"]["args_schema_fields"],
                [{"name": "value", "required": False, "schema": "string"}],
            )
            self.assertEqual(payload["human_help_surface"]["what_it_does"], "Explains the skill.")


if __name__ == "__main__":
    unittest.main()
