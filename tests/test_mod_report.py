import datetime
import time
import unittest
from types import SimpleNamespace

from bot.mod_report import describe, due, split, week_stats
from bot.members import day


class ModReportTests(unittest.TestCase):
    def test_due_first_start_and_mondays(self):
        mon = datetime.datetime(2026, 10, 12, 8, 5, tzinfo=datetime.timezone.utc)
        self.assertTrue(due(mon, ""))
        self.assertTrue(due(mon, "2026-10-07"))
        self.assertFalse(due(mon, "2026-10-12"))
        self.assertFalse(due(mon.replace(hour=7), "2026-10-07"))
        self.assertFalse(due(mon + datetime.timedelta(days=1), "2026-10-07"))

    def test_week_stats(self):
        now = time.time()
        m = {"days": {day(now): 5, day(now - 2 * 86400): 3, day(now - 10 * 86400): 7,
                      day(now - 40 * 86400): 9}}
        self.assertEqual(week_stats(m, now), (8, 2, 7))

    def test_split_keeps_messages_short(self):
        text = "\n".join(f"line {i} " + "x" * 90 for i in range(60))
        parts = split(text, 500)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertEqual("".join(parts).count("line"), 60)
        self.assertEqual(split("a" * 1200, 500)[0], "a" * 500)

    def test_describe(self):
        t = SimpleNamespace(display_name="Nex")
        self.assertEqual(describe("ban", None, None, t, "spam"), "ban Nex (reason: spam)")
        self.assertEqual(describe("kick", None, None, t, None), "kick Nex (no reason)")
        after = SimpleNamespace(timed_out_until="2026-10-08")
        self.assertEqual(describe("member_update", None, after, t, ""), "timeout Nex (no reason)")
        self.assertIsNone(describe("member_update", None, SimpleNamespace(), t, ""))
        self.assertIsNone(describe("channel_update", None, None, t, ""))
        self.assertEqual(describe("message_delete", None, None, t, None, 3), "deleted 3 message(s) of Nex")


if __name__ == "__main__":
    unittest.main()
