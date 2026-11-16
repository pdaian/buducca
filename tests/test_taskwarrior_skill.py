import importlib.util
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from assistant_framework import taskwarrior as cli
from assistant_framework.cli import main
from assistant_framework.skills import SkillManager, build_skill_manifest
from assistant_framework.workspace import Workspace

UUID = "2e17b750-c137-4ddd-b6dd-c63e272107f4"


def load_taskwarrior_module():
    skill_path = Path("skills/taskwarrior/__init__.py")
    spec = importlib.util.spec_from_file_location("taskwarrior_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load taskwarrior skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TaskwarriorSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_taskwarrior_module()
        self.workspace = Workspace(".")
        self.process = self.enterContext(patch.object(cli.subprocess, "run"))
        self.process.return_value = subprocess.CompletedProcess(["task"], 0, "[]", "")

    def run_skill(self, args):
        return json.loads(self.module.run(self.workspace, args))

    def test_list_exports_json_and_preserves_unknown_fields(self) -> None:
        tasks = [{"uuid": UUID, "id": 1, "status": "pending", "description": "Buy milk", "custom": 7}]
        self.process.return_value.stdout = json.dumps(tasks)
        result = self.run_skill({})
        self.assertEqual(result, {"ok": True, "action": "list", "tasks": tasks})
        self.assertEqual(self.process.call_args.args[0][-1], "export")

    def test_commands_are_non_interactive_and_use_harness_database(self) -> None:
        self.run_skill({"action": "list"})
        command = self.process.call_args.args[0]
        self.assertEqual(command[0], "task")
        for flag in ("rc.bulk=0", "rc.confirmation=off", "rc.recurrence.confirmation=no",
                     "rc.json.array=on", "rc.color=off", "rc.context=", "rc.allow.empty.filter=off"):
            self.assertIn(flag, command)
        self.assertFalse(any(arg.startswith(("rc.data.location", "rc:")) for arg in command))
        self.assertEqual(self.process.call_args.kwargs, {
            "stdin": subprocess.DEVNULL, "capture_output": True, "text": True,
            "encoding": "utf-8", "check": False, "timeout": 30,
        })

    def test_list_accepts_legacy_command_alias(self) -> None:
        self.assertTrue(self.run_skill({"command": "list"})["ok"])
        self.assertTrue(self.run_skill({"action": "LIST", "command": "list"})["ok"])

    def test_list_keeps_command_named_search_terms_read_only(self) -> None:
        result = self.run_skill({"filter": ["delete", "+PENDING"]})
        self.assertTrue(result["ok"])
        self.assertEqual(self.process.call_args.args[0][-3:], ["export", "delete", "+PENDING"])

    def test_list_handles_empty_results_and_explicit_filters(self) -> None:
        result = self.run_skill({"filter": ["project:Home", "status:pending"]})
        self.assertEqual(result["tasks"], [])
        self.assertEqual(self.process.call_args.args[0][-3:], ["export", "project:Home", "status:pending"])

    def test_string_filter_preserves_quoted_terms(self) -> None:
        for filters in ('description.contains:"annual report" +PENDING',
                        ["description.contains:annual report", "+PENDING"],
                        ['description.contains:"annual report"', "+PENDING"]):
            with self.subTest(filters=filters):
                self.run_skill({"filter": filters})
                self.assertEqual(self.process.call_args.args[0][-2:], ['description.contains:"annual report"', "+PENDING"])

    def test_rejects_invalid_arguments_before_any_cli_call(self) -> None:
        cases = [
            None, [], {"action": None}, {"action": "delete"},
            {"action": "list", "command": "done"}, {"action": "list", "due": "tomorrow"},
            {"action": "add"}, {"action": "add", "description": None},
            {"action": "add", "description": " "}, {"action": "add", "description": 3},
            {"action": "add", "description": "x", "due": None},
            {"action": "add", "description": "x", "project": {"x": 1}},
            {"action": "done"}, {"action": "done", "tasks": []},
            {"action": "done", "tasks": [UUID], "filter": "+PENDING"},
            {"action": "modify", "tasks": [1]}, {"action": "modify", "tasks": [1], "dues": "2d"},
            {"filter": None}, {"filter": [None]}, {"filter": [""]}, {"filter": '"unclosed'},
            {"filter": ["rc.data.location=/other"]}, {"filter": "rc:/other"},
            {"filter": ["--", "delete"]}, {"filter": ["x\0"]},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_skill(args)
                self.assertFalse(result["ok"])
                self.assertTrue(result["error"])
        self.process.assert_not_called()

    def test_write_targets_cannot_be_filters_or_commands(self) -> None:
        for target in (True, 0, -1, 1.5, None, {}, "+PENDING", "1-5", "1,2", "done", "rc.confirmation=off", "abc", ""):
            with self.subTest(target=target):
                self.assertFalse(self.run_skill({"action": "done", "tasks": [target]})["ok"])
        self.process.assert_not_called()

    def test_add_protects_literal_description_and_quotes_field_values(self) -> None:
        self.process.return_value.stdout = "Created task 5."
        description = "project:Elsewhere +urgent $(touch unwanted)"
        result = self.run_skill({"action": "add", "description": description, "project": "Deep Work", "due": "tomorrow"})
        self.assertEqual(result, {"ok": True, "action": "add", "output": "Created task 5."})
        self.assertEqual(self.process.call_args.args[0][-6:], [
            "add", "rc.verbose=new-id,affected", 'project:"Deep Work"', 'due:"tomorrow"', "--", description,
        ])

    def test_modify_can_clear_fields_and_replace_description(self) -> None:
        self.process.return_value.stdout = "Modified 1 task."
        result = self.run_skill({"action": "modify", "tasks": [UUID], "due": "", "project": "", "description": "+literal"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["targets"], [UUID])
        self.assertEqual(self.process.call_args.args[0][-7:], [
            UUID, "modify", "rc.verbose=new-id,affected", "project:", "due:", "--", "+literal",
        ])

    def test_done_normalizes_and_deduplicates_ids_and_uuids(self) -> None:
        result = self.run_skill({"action": "done", "tasks": [3, "3", UUID.upper(), UUID]})
        self.assertEqual(result["targets"], ["3", UUID])
        self.assertEqual(self.process.call_args.args[0][-4:], ["3", UUID, "done", "rc.verbose=new-id,affected"])

    def test_list_rejects_malformed_exports(self) -> None:
        for stdout in ("not json", "", "null", "{}", '["task"]', '[{}, 1]'):
            with self.subTest(stdout=stdout):
                self.process.return_value.stdout = stdout
                self.assertFalse(self.run_skill({"action": "list"})["ok"])

    def test_cli_failures_are_structured_errors(self) -> None:
        for failure in (FileNotFoundError(), PermissionError("denied"), subprocess.TimeoutExpired(["task"], 30)):
            with self.subTest(failure=failure):
                self.process.side_effect = failure
                result = self.run_skill({"action": "done", "tasks": [UUID]})
                self.assertFalse(result["ok"])
                self.assertTrue(result["error"])
                if isinstance(failure, subprocess.TimeoutExpired):
                    self.assertIn("before retrying", result["error"])

    def test_command_failure_reports_stderr_or_stdout(self) -> None:
        for stdout, stderr in (("", "Hook refused update"), ("Task not found", ""), ("", "")):
            with self.subTest(stdout=stdout, stderr=stderr):
                self.process.return_value = subprocess.CompletedProcess(["task"], 1, stdout, stderr)
                result = self.run_skill({"action": "modify", "tasks": [UUID], "due": "tomorrow"})
                self.assertFalse(result["ok"])
                self.assertIn(stderr or stdout or "exit code 1", result["error"])

    def test_harness_discovers_both_skills_and_prompt_schemas(self) -> None:
        skills = SkillManager("skills").load()
        for name, required in (("taskwarrior", set()), ("advancetodos", {"amount"})):
            with self.subTest(name=name):
                manifest = build_skill_manifest(skills[name])
                fields = manifest["prompt_surface"]["args_schema_fields"]
                self.assertTrue(fields)
                self.assertEqual({field["name"] for field in fields if field["required"]}, required)
                self.assertTrue(manifest["requires_llm_response"])
                self.assertTrue(manifest["human_help_surface"]["what_it_does"])
        self.process.assert_not_called()

    def test_harness_cli_executes_both_skills(self) -> None:
        for name, args, expected in (
            ("taskwarrior", {"action": "list"}, {"tasks": []}),
            ("advancetodos", {"amount": "2d", "dry_run": True}, {"matched": 0, "changes": []}),
        ):
            with self.subTest(name=name):
                output = io.StringIO()
                argv = ["assistant_framework", "skill", name, "--skills", "skills", "--args", json.dumps(args)]
                with patch("sys.argv", argv), redirect_stdout(output):
                    main()
                result = json.loads(output.getvalue())
                self.assertTrue(result["ok"])
                for key, value in expected.items():
                    self.assertEqual(result[key], value)


if __name__ == "__main__":
    unittest.main()
