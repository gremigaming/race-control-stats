import random
import unittest

from bot import award_roles as ar
from bot import giveaway as g

CONFIG = g.load_config()


class GiveawayTests(unittest.TestCase):
    def test_fifth_sub_starts_the_countdown(self):
        s = g.fresh(1, 1000)
        for i in range(4):
            self.assertEqual(g.add_sub(s, {"user": f"u{i}", "gift": False}, 1000 + i, CONFIG), "count")
        self.assertEqual(g.subs_left(s, CONFIG), 1)
        self.assertEqual(g.add_sub(s, {"user": "u5", "gift": True}, 2000, CONFIG), "draw_soon")
        self.assertEqual(s["status"], "drawing")
        self.assertEqual(s["draw_at"], 2000 + 600)
        self.assertIsNone(g.add_sub(s, {"user": "late", "gift": False}, 2100, CONFIG))
        self.assertIsNone(g.due(s, 2599))
        self.assertEqual(g.due(s, 2600), "draw")

    def test_reopens_the_day_after_at_midnight(self):
        # Mon 13 Oct 2026 21:30 Amsterdam (19:30 UTC) -> Tue 14 Oct 00:00 Amsterdam (13 Oct 22:00 UTC)
        drawn = 1791919800
        self.assertEqual(g.reopen_time(drawn), 1791928800)
        s = dict(g.fresh(1, 0), status="closed", reopen_at=g.reopen_time(drawn))
        self.assertIsNone(g.due(s, drawn + 3600))
        self.assertEqual(g.due(s, 1791928800), "reopen")

    def test_entries_follow_roles(self):
        self.assertEqual(g.entries([], CONFIG), (10, []))
        total, extra = g.entries([1086959872731512942, 1080519811219595376, 1559540316644704320,
                                  1558583801813270581, 1472160002243428487, 1472162335224238144], CONFIG)
        self.assertEqual(total, 10 + 10 + 20 + 10 + 30 + 5 + 5)
        self.assertEqual(len(extra), 6)
        # SR D, E and F give nothing
        self.assertEqual(g.entries([1558583810080383117], CONFIG)[0], 10)
        self.assertEqual(g.entries([1558583809455161524], CONFIG)[0], 15)

    def test_pick_is_weighted(self):
        rng = random.Random(1)
        wins = {"a": 0, "b": 0}
        for _ in range(4000):
            wins[g.pick({"a": 10, "b": 30, "c": 0}, rng)] += 1
        self.assertAlmostEqual(wins["b"] / 4000, 0.75, delta=0.03)
        self.assertIsNone(g.pick({}))

    def test_chat_line(self):
        s = g.fresh(1, 0)
        self.assertEqual(g.status_line(s, 0, CONFIG), "Only 5 more new subs until the draw!")
        s["subs"] = [{}] * 4
        self.assertEqual(g.status_line(s, 0, CONFIG), "Only 1 more new sub until the draw!")
        s.update(status="drawing", draw_at=600)
        self.assertEqual(g.status_line(s, 0, CONFIG), "The draw is in 10 minutes!")

    def test_panel_and_table(self):
        text = g.entries_text(CONFIG)
        self.assertIn("Prize Hunter role: **10**", text)
        self.assertIn("Tier 3 **+30**", text)
        self.assertIn("S+ +30", text)
        body = g.panel(g.fresh(3, 0), CONFIG, 0)
        self.assertEqual(body["flags"], 1 << 15)
        self.assertIn("ROUND 3", str(body))


class AwardTests(unittest.TestCase):
    RANK = [{"name": "TTV/GreMi_Gaming", "text": "16 wins"}, {"name": "Shw1ks", "text": "4 wins"},
            {"name": "DDRK", "text": "1 win"}]
    LINKED = {"ttv/gremi_gaming": 1, "shw1ks": 2, "ddrk": 3}

    def test_never_gremi(self):
        uid, row = ar.choose({}, self.RANK, self.LINKED, {1, 2, 3}, 1, 0)
        self.assertEqual((uid, row["name"]), (2, "Shw1ks"))

    def test_holder_keeps_it_for_a_week(self):
        state = {"uid": 3, "since": 0}
        self.assertEqual(ar.choose(state, self.RANK, self.LINKED, {1, 2, 3}, 1, 6 * 86400)[0], 3)
        self.assertEqual(ar.choose(state, self.RANK, self.LINKED, {1, 2, 3}, 1, 7 * 86400)[0], 2)

    def test_holder_who_left_is_replaced(self):
        self.assertEqual(ar.choose({"uid": 3, "since": 0}, self.RANK, self.LINKED, {1, 2}, 1, 10)[0], 2)

    def test_unlinked_drivers_are_skipped(self):
        self.assertEqual(ar.choose({}, self.RANK, {"ddrk": 3}, {3}, 1, 0)[0], 3)
        self.assertEqual(ar.choose({}, self.RANK, {}, set(), 1, 0), (None, None))

    def test_vip_names(self):
        vips = [("hiddegaming", "HiddeGaming"), ("nobody_here", "Nobody_Here")]
        members = [(10, [None, "Hidde", "hiddegam1ng"]), (11, ["Someone", None, "someone"])]
        self.assertEqual(ar.vip_matches(vips, members), {10})


if __name__ == "__main__":
    unittest.main()
