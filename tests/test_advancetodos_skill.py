import copy
import importlib.util
import json
import os
import subprocess
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from assistant_framework import taskwarrior as cli
from assistant_framework.workspace import Workspace

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


def task(number=1, due="20261115T090000Z", status="pending", **extra):
    return {
        "uuid": f"00000000-0000-4000-8000-{number:012d}", "id": number,
        "description": f"Task {number}", "status": status, "due": due, **extra,
    }


def response(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["task"], returncode, stdout, stderr)


def exported(tasks):
    return response(json.dumps(tasks))


def load_advancetodos_module():
    path = Path("skills/advancetodos/__init__.py")
    spec = importlib.util.spec_from_file_location("advancetodos_skill", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load advancetodos skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AdvanceTodosSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_advancetodos_module()
        self.workspace = Workspace(".")
        self.process = self.enterContext(patch.object(cli.subprocess, "run"))
        self.process.return_value = exported([])
        self.clock = self.enterContext(patch.object(self.module, "datetime", wraps=datetime))
        self.clock.now.return_value = NOW
        self.addCleanup(time.tzset)
        self.enterContext(patch.dict(os.environ, {"TZ": "UTC"}))
        time.tzset()

    def run_skill(self, args):
        return json.loads(self.module.run(self.workspace, args))

    def mutations(self):
        return [call.args[0] for call in self.process.call_args_list if "modify" in call.args[0]]

    def preview(self, tasks, **args):
        self.process.return_value = exported(tasks)
        return self.run_skill({"amount": "2d", "dry_run": True, **args})

    def test_default_selection_includes_only_overdue_open_tasks(self) -> None:
        tasks = [
            task(1), task(2, "20261116T115959Z", "waiting"),
            task(3, "20261116T120000Z"), task(4, "20261116T120001Z"),
            task(5, status="completed"), task(6, status="deleted"),
            task(7, status="recurring"), task(8, due=None),
            task(9, "20270716T090000Z", description="renew progressive"),
            task(10, "20261201T090000Z", "waiting"),
        ]
        for args in ({}, {"scope": "overdue"}):
            with self.subTest(args=args):
                self.process.reset_mock()
                result = self.preview(tasks, **args)
                self.assertTrue(result["ok"])
                self.assertEqual(result["scope"], "overdue")
                self.assertEqual(result["matched"], 2)
                self.assertEqual(result["updated_count"], 0)
                self.assertEqual([item["uuid"] for item in result["changes"]], [tasks[0]["uuid"], tasks[1]["uuid"]])
                self.assertEqual(self.process.call_count, 1)
                self.assertEqual(self.mutations(), [])

    def test_explicit_all_scope_includes_future_dated_open_tasks(self) -> None:
        tasks = [
            task(1), task(2, "20261116T115959Z", "waiting"),
            task(3, "20261116T120000Z"), task(4, "20261116T120001Z"),
            task(5, status="completed"), task(6, status="deleted"),
            task(7, status="recurring"), task(8, due=None),
            task(9, "20261201T090000Z", "waiting"),
        ]
        result = self.preview(tasks, scope="all")
        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "all")
        self.assertEqual(result["matched"], 5)
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual([item["uuid"] for item in result["changes"]], [tasks[i]["uuid"] for i in (0, 1, 2, 3, 8)])
        self.assertEqual([item["new_due"] for item in result["changes"]], [
            "20261117T090000Z", "20261118T115959Z", "20261118T120000Z",
            "20261118T120001Z", "20261203T090000Z",
        ])
        self.assertEqual(self.process.call_count, 1)
        self.assertEqual(self.mutations(), [])

    def test_apply_shifts_each_original_due_and_verifies_by_uuid(self) -> None:
        first = task(1, "20261101T090000Z", custom="preserve", scheduled="20261031T090000Z")
        second = task(2, "20261201T183000Z", "waiting", wait="20261120T000000Z")
        original = copy.deepcopy([first, second])
        cases = [
            ("2d", 172800, ["20261103T090000Z", "20261203T183000Z"]),
            ("-2d", -172800, ["20261030T090000Z", "20261129T183000Z"]),
        ]
        for amount, seconds, due_dates in cases:
            with self.subTest(amount=amount):
                self.process.reset_mock()
                self.process.side_effect = [
                    exported([first, second]), response(), exported([{**first, "due": due_dates[0]}]),
                    response(), exported([{**second, "due": due_dates[1]}]),
                ]
                result = self.run_skill({"amount": amount, "scope": "all"})
                self.assertTrue(result["ok"])
                self.assertFalse(result["dry_run"])
                self.assertEqual(result["scope"], "all")
                self.assertEqual(result["matched"], 2)
                self.assertEqual(result["updated_count"], 2)
                self.assertEqual(result["amount_seconds"], seconds)
                self.assertEqual([item["new_due"] for item in result["updated"]], due_dates)
                self.assertEqual([first, second], original)
                for command, source, change in zip(self.mutations(), original, result["updated"]):
                    self.assertIn(source["uuid"], command)
                    self.assertNotIn(str(source["id"]), command)
                    self.assertIn(f"due.is:{source['due']}", command)
                    self.assertIn("status:pending", command)
                    self.assertIn("status:waiting", command)
                    self.assertEqual(command[command.index("modify") + 1:], [f"due:{change['new_due']}"])
                self.assertEqual(self.process.call_count, 5)

    def test_210_day_apply_excludes_future_task_and_verifies_each_update(self) -> None:
        future = task(1, "20270716T090000Z", description="renew progressive")
        first, second = task(2, "20260601T090000Z"), task(3, "20260602T090000Z", "waiting")
        self.process.side_effect = [
            exported([future, first, second]),
            response(), exported([{**first, "due": "20261228T090000Z"}]),
            response(), exported([{**second, "due": "20261229T090000Z"}]),
        ]
        result = self.run_skill({"amount": "210d"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "overdue")
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["updated_count"], 2)
        self.assertEqual([item["uuid"] for item in result["updated"]], [first["uuid"], second["uuid"]])
        for command in self.mutations():
            self.assertNotIn(future["uuid"], command)
        reads = [call.args[0] for call in self.process.call_args_list if "export" in call.args[0]]
        self.assertEqual([command[-1] for command in reads], ["export"] * 3)
        self.assertEqual(reads[1][-2], f"( {first['uuid']} )")
        self.assertEqual(reads[2][-2], f"( {second['uuid']} )")

    def test_overdue_selection_uses_one_utc_cutoff_in_any_timezone(self) -> None:
        tasks = [task(1, "20261116T115959Z"), task(2, "20261116T120000Z"), task(3, "20261116T120001Z")]
        with patch.dict(os.environ, {"TZ": "America/New_York"}):
            time.tzset()
            result = self.preview(tasks)
        time.tzset()
        self.assertEqual([item["uuid"] for item in result["changes"]], [tasks[0]["uuid"]])
        self.clock.now.assert_called_once_with(timezone.utc)

    def test_inclusive_date_range_restricts_selection(self) -> None:
        tasks = [
            task(1, "20261201T000000Z"), task(2, "20261203T235959Z"),
            task(3, "20261130T235959Z"), task(4, "20261204T000000Z"),
            task(5, "20261202T120000Z", "waiting"), task(6, "20261202T120000Z", "completed"),
        ]
        for args in ({}, {"scope": "date_range"}):
            with self.subTest(args=args):
                result = self.preview(tasks, start_date="2026-12-01", end_date="2026-12-03", **args)
                self.assertTrue(result["ok"])
                self.assertEqual(result["scope"], "date_range")
                self.assertEqual([item["uuid"] for item in result["changes"]], [tasks[i]["uuid"] for i in (0, 1, 4)])

    def test_date_range_applies_instead_of_only_previewing(self) -> None:
        original = task(1, "20261201T010000Z")
        for amount, new_due in (("PT90M", "20261201T023000Z"), ("-PT90M", "20261130T233000Z")):
            with self.subTest(amount=amount):
                self.process.side_effect = [exported([original]), response(), exported([{**original, "due": new_due}])]
                result = self.run_skill({"amount": amount, "start_date": "2026-12-01", "end_date": "2026-12-01"})
                self.assertTrue(result["ok"])
                self.assertEqual(result["updated_count"], 1)
                self.assertEqual(result["updated"][0]["new_due"], new_due)

    def test_date_range_uses_harness_timezone_and_full_end_day(self) -> None:
        with patch.dict(os.environ, {"TZ": "America/New_York"}):
            time.tzset()
            tasks = [
                task(1, "20261116T045959Z"), task(2, "20261116T050000Z"),
                task(3, "20261117T045959Z"), task(4, "20261117T050000Z"),
            ]
            result = self.preview(tasks, start_date="2026-11-16", end_date="2026-11-16")
        time.tzset()
        self.assertTrue(result["ok"])
        self.assertEqual([item["uuid"] for item in result["changes"]], [tasks[1]["uuid"], tasks[2]["uuid"]])

    def test_fixed_days_across_dst_and_leap_day(self) -> None:
        cases = [
            ("20261031T160000Z", "1d", "20261101T160000Z"),
            ("20261101T160000Z", "-1d", "20261031T160000Z"),
            ("20240228T235959Z", "1d", "20240229T235959Z"),
            ("20240301T235959Z", "-1d", "20240229T235959Z"),
        ]
        with patch.dict(os.environ, {"TZ": "America/New_York"}):
            time.tzset()
            for old_due, amount, new_due in cases:
                with self.subTest(old_due=old_due, amount=amount):
                    result = self.preview([task(1, old_due)], amount=amount)
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["changes"][0]["new_due"], new_due)
                    self.assertEqual(result["updated_count"], 0)
        time.tzset()
        self.assertEqual(self.mutations(), [])

    def test_range_uses_offset_at_due_date_not_current_season(self) -> None:
        with patch.dict(os.environ, {"TZ": "America/New_York"}):
            time.tzset()
            tasks = [task(1, "20260616T035959Z"), task(2, "20260616T040000Z")]
            result = self.preview(tasks, start_date="2026-06-16", end_date="2026-06-16")
        time.tzset()
        self.assertTrue(result["ok"])
        self.assertEqual([item["uuid"] for item in result["changes"]], [tasks[1]["uuid"]])

    def test_recurring_instances_are_updated_without_template_or_propagation(self) -> None:
        child = task(1, parent=task(2)["uuid"], recur="weekly")
        sibling = task(3, "20261201T090000Z", parent=task(2)["uuid"], recur="weekly")
        self.process.side_effect = [
            exported([child, task(2, status="recurring"), sibling]),
            response(), exported([{**child, "due": "20261117T090000Z"}]),
            response(), exported([{**sibling, "due": "20261203T090000Z"}]),
        ]
        result = self.run_skill({"amount": "2d", "scope": "all"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["updated_count"], 2)
        self.assertEqual([item["uuid"] for item in result["updated"]], [child["uuid"], sibling["uuid"]])
        for command, selected, other in zip(self.mutations(), (child, sibling), (sibling, child)):
            self.assertIn("rc.recurrence.confirmation=no", command)
            self.assertIn(selected["uuid"], command)
            self.assertNotIn(other["uuid"], command)
            self.assertNotIn(task(2)["uuid"], command)

    def test_no_matches_performs_no_writes(self) -> None:
        result = self.run_skill({"amount": "1h"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["updated"], [])
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(self.process.call_count, 1)

    def test_supported_duration_formats(self) -> None:
        cases = {"2d": 172800, "1 week": 604800, "1h 30min": 5400, "1.5h": 5400,
                 "60 seconds": 60, "P2D": 172800, "P1W": 604800, "PT90M": 5400,
                 "P1DT2H3M4S": 93784, "  2 DAYS  ": 172800, "0.5min": 30}
        for value, expected in cases.items():
            for prefix, sign in (("", 1), ("+", 1), ("-", -1)):
                amount = prefix + value
                with self.subTest(amount=amount):
                    self.assertEqual(self.module._duration(amount), timedelta(seconds=sign * expected))

    def test_invalid_inputs_do_not_read_or_write_tasks(self) -> None:
        cases = [None, [], {}, {"amount": "1d", "duration": "2d"},
                 {"amount": "1d", "dry_run": "false"}, {"amount": "1d", "dry_run": 1},
                 {"amount": "1d", "scope": "pending"}, {"amount": "1d", "scope": None},
                 {"amount": "1d", "scope": True}, {"amount": "1d", "scope": []},
                 {"amount": "1d", "scope": "date_range"},
                 {"amount": "1d", "scope": "all", "start_date": "2026-01-01", "end_date": "2026-01-02"},
                 {"amount": "1d", "scope": "overdue", "start_date": "2026-01-01", "end_date": "2026-01-02"},
                 {"amount": "1d", "start_date": "2026-01-01"},
                 {"amount": "1d", "end_date": "2026-01-01"},
                 {"amount": "1d", "start_date": None, "end_date": None},
                 {"amount": "1d", "start_date": "2026-02-30", "end_date": "2026-03-01"},
                 {"amount": "1d", "start_date": "20261116", "end_date": "2026-11-17"},
                 {"amount": "1d", "start_date": "2026-11-18", "end_date": "2026-11-17"}]
        for amount in (None, 2, True, "", "0d", "-0d", "+0s", "-PT0S", "2", "1m", "1 month", "1y", "P1M", "P1Y",
                       "P", "PT", "P1DT", "PT0.1S", "0.001s", "tomorrow", "1d; delete", "999999999999999999999d",
                       "-", "+", "--1d", "++1d", "+-1d", "-+1d", "1h -30min", "-1h +30min", "-1h -30min",
                       "-P1M", "-PT0.1S", "-999999999999999999999d"):
            cases.append({"amount": amount})
        for args in cases:
            with self.subTest(args=args):
                result = self.run_skill(args)
                self.assertFalse(result["ok"])
                self.assertEqual(result["updated_count"], 0)
        self.process.assert_not_called()

    def test_export_failures_do_not_write(self) -> None:
        for output in ("invalid", "{}", "null", '["task"]'):
            with self.subTest(output=output):
                self.process.return_value = response(output)
                result = self.run_skill({"amount": "1d"})
                self.assertFalse(result["ok"])
                self.assertEqual(result["updated_count"], 0)
        self.assertEqual(self.mutations(), [])

    def test_cli_read_failure_is_reported_without_writes(self) -> None:
        self.process.side_effect = FileNotFoundError()
        result = self.run_skill({"amount": "1d"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(self.mutations(), [])

    def test_entire_plan_is_validated_before_writing(self) -> None:
        bad_tasks = [task(2, due="bad"), task(2, due="20260230T000000Z"),
                     {**task(2), "uuid": "not-a-uuid"}, task(2, status=[]), task(1)]
        for invalid in bad_tasks:
            with self.subTest(invalid=invalid):
                self.process.return_value = exported([task(1), invalid])
                result = self.run_skill({"amount": "1d"})
                self.assertFalse(result["ok"])
                self.assertEqual(result["updated_count"], 0)
        self.assertEqual(self.mutations(), [])

    def test_due_overflow_and_underflow_fail_before_writing(self) -> None:
        for amount, due in (("1d", "99991231T235959Z"), ("-1d", "00010101T000000Z")):
            with self.subTest(amount=amount):
                self.process.return_value = exported([task(1), task(2, due)])
                result = self.run_skill({"amount": amount, "scope": "all"})
                self.assertFalse(result["ok"])
                self.assertIn("supported date range", result["error"])
                self.assertEqual(result["updated_count"], 0)
        self.assertEqual(self.mutations(), [])

    def test_partial_failure_stops_and_preserves_verified_progress(self) -> None:
        first, second, third = task(1), task(2), task(3)
        self.process.side_effect = [
            exported([first, second, third]), response(), exported([{**first, "due": "20261117T090000Z"}]),
            response(returncode=1, stderr="Hook rejected change"),
        ]
        result = self.run_skill({"amount": "2d"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["matched"], 3)
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["updated"][0]["uuid"], first["uuid"])
        self.assertEqual(result["failed"]["uuid"], second["uuid"])
        self.assertEqual(result["failed"]["outcome"], "unknown")
        self.assertIn("Hook rejected change", result["failed"]["error"])
        self.assertIn("Hook rejected change", result["error"])
        self.assertEqual(result["unattempted_count"], 1)
        self.assertEqual(len(self.mutations()), 2)

    def test_timeout_does_not_retry_or_claim_a_verified_update(self) -> None:
        self.process.side_effect = [exported([task()]), subprocess.TimeoutExpired(["task"], 30)]
        result = self.run_skill({"amount": "1d"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["failed"]["outcome"], "unknown")
        self.assertIn("before retrying", result["error"])
        self.assertEqual(len(self.mutations()), 1)

    def test_failed_verification_does_not_claim_success(self) -> None:
        for actual in ([task()], [], [task(2, "20261117T090000Z")]):
            with self.subTest(actual=actual):
                self.process.side_effect = [exported([task()]), response(), exported(actual)]
                result = self.run_skill({"amount": "2d"})
                self.assertFalse(result["ok"])
                self.assertEqual(result["updated_count"], 0)
                self.assertEqual(result["failed"]["outcome"], "unknown")

    def test_verification_cli_failure_preserves_uncertainty(self) -> None:
        self.process.side_effect = [exported([task()]), response(), response("not json")]
        result = self.run_skill({"amount": "1d"})
        self.assertFalse(result["ok"])
        self.assertIn("invalid JSON", result["failed"]["error"])
        self.assertEqual(result["updated_count"], 0)


if __name__ == "__main__":
    unittest.main()
