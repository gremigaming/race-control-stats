import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "discord"))
import mod_data  # noqa: E402


class ModDataTests(unittest.TestCase):
    def test_windows(self):
        today = 100 * 86400
        dates = [today - 3600, today - 31 * 86400, today - 61 * 86400, today - 95 * 86400]
        self.assertEqual(mod_data.windows(dates, today), [1, 1, 1])

    def test_split_keeps_messages_short(self):
        text = "\n".join(f"line {i} " + "x" * 90 for i in range(60))
        parts = mod_data.split(text, 500)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertEqual("".join(parts).count("line"), 60)
        self.assertEqual(mod_data.split("a" * 1200, 500)[0], "a" * 500)

    def test_describe(self):
        names = {"1": "Nex"}
        self.assertEqual(mod_data.describe({"action_type": 22, "target_id": "1", "reason": "spam"}, names),
                         "ban Nex (reason: spam)")
        self.assertEqual(mod_data.describe({"action_type": 20, "target_id": "2"}, names),
                         "kick someone (no reason)")
        timeout = {"action_type": 24, "target_id": "1",
                   "changes": [{"key": "communication_disabled_until", "new_value": "2026-10-08"}]}
        self.assertEqual(mod_data.describe(timeout, names), "timeout Nex (no reason)")
        self.assertIsNone(mod_data.describe({"action_type": 24, "changes": [{"key": "nick"}]}, names))
        self.assertIsNone(mod_data.describe({"action_type": 10}, names))
        self.assertEqual(mod_data.describe({"action_type": 72, "target_id": "1", "options": {"count": "3"}}, names),
                         "deleted 3 message(s) of Nex")

    def test_newest_keeps_latest(self):
        self.assertEqual(mod_data.newest(["aaa", "bbb", "ccc"], 8), "bbb\nccc")


if __name__ == "__main__":
    unittest.main()
