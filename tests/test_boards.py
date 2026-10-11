"""Tests for bot/boards.py (no Discord needed)."""
import datetime
import unittest

from bot import boards

ROW = {"name": "DDRK", "points": 54, "races": 5, "wins": 0, "podiums": 1}


class ChampionshipTests(unittest.TestCase):
    def test_this_month(self):
        data = {"months": {"2026-10": [ROW], "2026-09": [dict(ROW, name="Old")]}}
        title, blocks, footer = boards.championship(data, datetime.date(2026, 10, 11))
        self.assertEqual(title, "F1 championship: October 2026")
        self.assertIn(" 1   54   0  1   5  DDRK", blocks[0])
        self.assertNotIn("Old", blocks[0])
        self.assertIn("Pts points", footer)

    def test_new_month_without_races_shows_last_one_as_final(self):
        title, blocks, _ = boards.championship({"months": {"2026-10": [ROW]}}, datetime.date(2026, 11, 1))
        self.assertEqual(title, "F1 championship: October 2026")
        self.assertIn("Final standings of October 2026", blocks[0])

    def test_empty(self):
        self.assertEqual(boards.championship({}, datetime.date(2026, 10, 1))[1], ["No races yet."])


class SafetyTests(unittest.TestCase):
    def test_ranking_leaves_out_removed_and_hidden_drivers(self):
        data = {"drivers": [
            {"name": "DDRK", "sr": 58.5, "change": 8.5},
            {"name": "Rookie", "sr": 50.0, "change": 0, "provisional": True},
            {"name": "Ferrari '26 #28", "sr": 40.0},
            {"name": "Wrecker", "sr": 1.0, "banned": True}]}
        _, blocks, _ = boards.safety(data)
        self.assertIn(" 1 B   58.5   +8.5  DDRK", blocks[0])
        self.assertIn(" 2 B   50.0*     -  Rookie", blocks[0])
        self.assertNotIn("#28", blocks[0])
        self.assertNotIn("Wrecker", blocks[0])

    def test_long_lists_fit_one_message(self):
        data = {"drivers": [{"name": f"Driver number {i:03d}", "sr": 50, "change": -1.5} for i in range(300)]}
        _, blocks, footer = boards.safety(data)
        self.assertTrue(all(len(b) <= 4096 for b in blocks))
        self.assertLessEqual(sum(map(len, blocks)) + len(footer) + 20, 6000)
        self.assertLessEqual(len(blocks), 10)
        self.assertIn("more on the site", footer)


if __name__ == "__main__":
    unittest.main()
