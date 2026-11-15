import unittest

from assistant_framework.execution import RepeatedResultGuard, bound_skill_result, read_skill_result


class RepeatedResultGuardTests(unittest.TestCase):
    def test_argument_order_does_not_hide_a_repeated_result(self) -> None:
        guard = RepeatedResultGuard(3)
        self.assertFalse(guard.observe("file", {"path": "x", "action": "read"}, "unchanged"))
        self.assertFalse(guard.observe("file", {"action": "read", "path": "x"}, "unchanged"))
        self.assertTrue(guard.observe("file", {"path": "x", "action": "read"}, "unchanged"))

    def test_progress_and_intervening_calls_reset_the_count(self) -> None:
        guard = RepeatedResultGuard(2)
        calls = [
            ("poll", {"id": 1}, "pending"),
            ("poll", {"id": 1}, "complete"),
            ("poll", {"id": 2}, "complete"),
            ("file", {"id": 2}, "complete"),
            ("poll", {"id": 2}, "complete"),
        ]
        for call in calls:
            self.assertFalse(guard.observe(*call))
        self.assertTrue(guard.observe(*calls[-1]))

    def test_disabled_guard_allows_identical_polling(self) -> None:
        guard = RepeatedResultGuard(None)
        for _ in range(20):
            self.assertFalse(guard.observe("poll", {}, "pending"))


class SkillResultTests(unittest.TestCase):
    def test_small_results_and_disabled_limit_are_unchanged(self) -> None:
        for result, limit in (("", 512), ("x" * 512, 512), ("x" * 10000, None)):
            self.assertEqual(bound_skill_result(result, step=1, max_chars=limit), result)

    def test_large_unicode_result_preserves_both_ends_within_limit(self) -> None:
        result = "BEGIN " + "é🙂" * 1000 + " ERROR AT END"
        excerpt = bound_skill_result(result, step=12, max_chars=512)
        self.assertEqual(len(excerpt), 512)
        self.assertTrue(excerpt.startswith("BEGIN "))
        self.assertTrue(excerpt.endswith("ERROR AT END"))
        self.assertIn("step 12 truncated", excerpt)
        self.assertIn("read_skill_result", excerpt)

    def test_ranges_recover_the_entire_original_result(self) -> None:
        result = "start\n" + "🙂é\n" * 500 + "end"
        steps = [{"skill_result": result}]
        pages = []
        offset = 0
        while offset < len(result):
            page = read_skill_result(steps, {"step": 1, "offset": offset, "limit": 10000}, max_chars=512)
            self.assertLessEqual(len(page), 512)
            _, _, body = page.partition("\n")
            self.assertTrue(body)
            pages.append(body)
            offset += len(body)
        self.assertEqual("".join(pages), result)

    def test_invalid_ranges_and_foreign_steps_cannot_be_read(self) -> None:
        steps = [{"skill_result": "private"}]
        for args in (
            {}, {"step": True}, {"step": "1"}, {"step": 0}, {"step": -1}, {"step": 2},
            {"step": 1, "offset": -1}, {"step": 1, "offset": True}, {"step": 1, "offset": 100},
            {"step": 1, "limit": 0}, {"step": 1, "limit": 1.5},
        ):
            with self.subTest(args=args):
                reply = read_skill_result(steps, args, max_chars=512)
                self.assertTrue(reply.startswith("read_skill_result:"))
                self.assertNotIn("private", reply)

    def test_exact_end_is_an_empty_page(self) -> None:
        page = read_skill_result([{"skill_result": "abc"}], {"step": 1, "offset": 3}, max_chars=512)
        self.assertIn("characters 3:3 of 3", page)
        self.assertEqual(page.partition("\n")[2], "")
