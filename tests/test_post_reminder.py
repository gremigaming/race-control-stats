import datetime
import unittest

import post_reminder as pr


def at(month, hour, minute):
    return datetime.datetime(2026, month, 13, hour, minute, tzinfo=pr.TZ)


class WaitTest(unittest.TestCase):
    def test_summer_runs(self):
        # 13:50 and 14:50 UTC in summer are 15:50 and 16:50 in Amsterdam
        self.assertEqual(pr.wait_seconds(at(7, 15, 50)), 30 * 60)
        self.assertIsNone(pr.wait_seconds(at(7, 16, 50)))

    def test_winter_runs(self):
        # 13:50 and 14:50 UTC in winter are 14:50 and 15:50 in Amsterdam
        self.assertIsNone(pr.wait_seconds(at(12, 14, 50)))
        self.assertEqual(pr.wait_seconds(at(12, 15, 50)), 30 * 60)

    def test_late_start_sends_now(self):
        self.assertEqual(pr.wait_seconds(at(7, 16, 35)), 0)


if __name__ == "__main__":
    unittest.main()
