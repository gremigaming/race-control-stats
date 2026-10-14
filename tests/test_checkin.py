import unittest

from bot import checkin as c

CONFIG = dict(c.load_config(), on=True)
DAY = 86400
NOW = 1791990000  # Wed 14 Oct 2026 17:00 Amsterdam


def member(i, joined_days_ago, roles=(), bot=False):
    return {"id": i, "joined": NOW - joined_days_ago * DAY, "bot": bot, "roles": list(roles)}


class CheckinTests(unittest.TestCase):
    def test_only_long_time_quiet_members(self):
        members = [member(1, 400), member(2, 400), member(3, 30), member(4, 500, bot=True),
                   member(5, 500, roles=[1080524470332174437]), member(6, 600), member(7, 700),
                   member(8, 800)]
        last = {2: NOW - 10 * DAY, 6: NOW - 300 * DAY}
        got = c.candidates(members, last, NOW, CONFIG, owner_id=8, asked={"7": NOW})
        # 2 posted recently, 3 is new, 4 is a bot, 5 is staff, 7 was asked, 8 is GreMi
        self.assertEqual(got, [6, 1])

    def test_pace(self):
        s = dict(c.fresh(), queue=[1, 2], day="2026-10-14")
        self.assertTrue(c.may_send(s, NOW, CONFIG))
        s["last_sent"] = NOW - 60
        self.assertFalse(c.may_send(s, NOW, CONFIG))  # 30 minutes between DMs
        s["last_sent"] = NOW - c.gap(CONFIG)
        self.assertTrue(c.may_send(s, NOW, CONFIG))
        s["sent_today"] = CONFIG["per_day"]
        self.assertFalse(c.may_send(s, NOW, CONFIG))
        s["sent_today"] = 0
        self.assertFalse(c.may_send(s, NOW - 8 * 3600, CONFIG))  # 09:00, too early
        self.assertFalse(c.may_send(s, NOW, dict(CONFIG, on=False)))

    def test_new_day_hands_back_the_old_one(self):
        s = dict(c.fresh(), day="2026-10-13", sent_today=20, dms_off=["x"])
        self.assertEqual(c.new_day(s, NOW), {"day": "2026-10-13", "sent": 20, "dms_off": ["x"]})
        self.assertEqual((s["day"], s["sent_today"], s["dms_off"]), ("2026-10-14", 0, []))
        self.assertIsNone(c.new_day(s, NOW))

    def test_reminder_then_removal(self):
        s = dict(c.fresh(), asked={"1": NOW, "2": NOW, "3": NOW, "4": NOW},
                 dm={"1": 11, "2": 12, "4": 14}, answers={"2": "yes"})
        never = lambda uid: 0
        week = 7 * DAY
        self.assertIsNone(c.follow_up(s, NOW + week - 60, CONFIG, never))
        # 2 answered, 3 has DMs off: only 1 and 4
        self.assertEqual(c.follow_up(s, NOW + week, CONFIG, never), ("1", "remind"))
        s["reminded"]["1"] = NOW + week
        self.assertEqual(c.follow_up(s, NOW + week, CONFIG, never), ("4", "remind"))
        s["reminded"]["4"] = NOW + week
        self.assertIsNone(c.follow_up(s, NOW + 2 * week - 60, CONFIG, never))
        # 4 posted in the server after the DM: never removed
        posted = lambda uid: NOW + DAY if uid == "4" else 0
        self.assertEqual(c.follow_up(s, NOW + 2 * week, CONFIG, posted), ("1", "kick"))
        s["answers"]["1"] = "no answer"
        self.assertIsNone(c.follow_up(s, NOW + 3 * week, CONFIG, posted))

    def test_text(self):
        t = c.text_for("Max", CONFIG)
        self.assertTrue(t.startswith("**Remember GreMi_Gaming? 🏎️**"))
        self.assertIn("Hey Max!", t)
        self.assertNotIn("scam", t)
        self.assertLess(len(t), 500)
        self.assertLess(len("\n".join(CONFIG["welcome"])), 2000)


if __name__ == "__main__":
    unittest.main()
