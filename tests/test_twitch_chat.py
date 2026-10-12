import unittest

from bot import twitch_chat as tc

CONFIG = {"only_live": True, "gap": 10, "min_chat": 0, "timers": [
    {"text": "discord", "first": 15, "every": 45},
    {"text": "site", "first": 30, "every": 60},
]}
ADS = {"auto": True, "length": 90, "min_gap": 20, "snooze_while_driving": True}


class SealTests(unittest.TestCase):
    def test_round_trip_needs_the_same_bot_token(self):
        login = {"client_id": "a", "client_secret": "b", "refresh_token": "c"}
        text = tc.seal(login, "discord-token")
        self.assertNotIn("refresh", text)
        self.assertEqual(tc.unseal(text, "discord-token"), login)
        with self.assertRaises(Exception):
            tc.unseal(text, "other-token")


class TimerTests(unittest.TestCase):
    def test_quiet_while_offline(self):
        self.assertIsNone(tc.next_timer(CONFIG, None, {}, 0, 10_000))

    def test_waits_for_first(self):
        self.assertIsNone(tc.next_timer(CONFIG, 10, {}, 0, 10_000))
        self.assertEqual(tc.next_timer(CONFIG, 16, {}, 0, 10_000), 0)

    def test_gap_between_messages(self):
        now = 100_000
        self.assertIsNone(tc.next_timer(CONFIG, 31, {0: now - 60}, now - 60, now))
        self.assertEqual(tc.next_timer(CONFIG, 41, {0: now - 600}, now - 600, now), 1)

    def test_repeats_after_every(self):
        now = 100_000
        sent = {0: now - 46 * 60, 1: now - 20 * 60}
        self.assertEqual(tc.next_timer(CONFIG, 90, sent, now - 20 * 60, now), 0)
        sent[0] = now - 30 * 60
        self.assertIsNone(tc.next_timer(CONFIG, 90, sent, now - 20 * 60, now))

    def test_waits_for_chat(self):
        config = dict(CONFIG, min_chat=3)
        self.assertIsNone(tc.next_timer(config, 16, {}, 0, 10_000, chatted=2))
        self.assertEqual(tc.next_timer(config, 16, {}, 0, 10_000, chatted=3), 0)

    def test_real_timers_file_loads(self):
        config = tc.load_timers()
        self.assertTrue(config["timers"])
        self.assertIn("{length}", config["ads"]["notice"])
        for t in config["timers"]:
            self.assertLessEqual(len(t["text"]), 500)
            self.assertGreater(t["every"], 0)


class AdTests(unittest.TestCase):
    now = 100_000

    def test_asked_runs_while_live_only(self):
        self.assertEqual(tc.ad_now(ADS, True, self.now, self.now - 60, 0, True), "asked")
        self.assertIsNone(tc.ad_now(ADS, False, self.now, 0, 0, True))

    def test_after_a_race(self):
        self.assertEqual(tc.ad_now(ADS, True, self.now, 0, self.now - 30, False), "race")
        # not twice for the same race, not too soon after the last ad, not when auto is off
        self.assertIsNone(tc.ad_now(ADS, True, self.now, self.now - 10, self.now - 30, False))
        self.assertIsNone(tc.ad_now(ADS, True, self.now, self.now - 600, self.now - 30, False))
        self.assertIsNone(tc.ad_now(dict(ADS, auto=False), True, self.now, 0, self.now - 30, False))
        self.assertIsNone(tc.ad_now(ADS, True, self.now, 0, self.now - 600, False))

    def test_snooze_while_driving(self):
        due = {"next_ad_at": str(self.now + 60), "snooze_count": 2}
        self.assertTrue(tc.snooze_now(ADS, due, self.now, 0))
        self.assertFalse(tc.snooze_now(ADS, due, self.now, self.now - 30))   # between races
        self.assertFalse(tc.snooze_now(ADS, dict(due, snooze_count=0), self.now, 0))
        self.assertFalse(tc.snooze_now(ADS, dict(due, next_ad_at=self.now + 900), self.now, 0))
        self.assertFalse(tc.snooze_now(dict(ADS, snooze_while_driving=False), due, self.now, 0))


if __name__ == "__main__":
    unittest.main()
