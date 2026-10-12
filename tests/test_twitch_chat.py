import unittest

from bot import twitch_chat as tc

CONFIG = {"only_live": True, "gap": 10, "min_chat": 0, "timers": [
    {"text": "discord", "first": 15, "every": 45},
    {"text": "site", "first": 30, "every": 60},
]}
ADS = {"auto": True, "length": 90, "race_length": 30, "target_per_hour": 180, "first": 10,
       "quiet_minutes": 3, "quiet_max": 1, "min_gap": 10, "lobby_after": 30,
       "snooze_when_busy": True}


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
        self.assertIn("{length}", config["ads"]["notice_lobby"])
        for t in config["timers"]:
            self.assertLessEqual(len(t["text"]), 500)
            self.assertGreater(t["every"], 0)


class AdTests(unittest.TestCase):
    now = 100_000

    def plan(self, **kw):
        args = dict(ads=ADS, now=self.now, live_minutes=60, last_race_ad=0, race_ended=0,
                    lobby_done=0, asked=False, recent_chat=0, hour_seconds=0, hour_minutes=15)
        args.update(kw)
        return tc.plan_ad(**args)

    def test_asked_runs_while_live_only(self):
        self.assertEqual(self.plan(asked=True, recent_chat=20), ("asked", 90))
        self.assertIsNone(self.plan(asked=True, live_minutes=None))

    def test_race_ad_when_chat_is_quiet(self):
        self.assertEqual(self.plan(), ("race", 30))
        self.assertIsNone(self.plan(recent_chat=2))                      # chat busy
        self.assertIsNone(self.plan(last_race_ad=self.now - 300))        # too soon
        self.assertIsNone(self.plan(hour_seconds=180))                   # hour is full
        self.assertIsNone(self.plan(live_minutes=5))                     # just went live
        self.assertIsNone(self.plan(ads=dict(ADS, auto=False)))

    def test_lobby_top_up(self):
        lobby = dict(race_ended=self.now - 30, hour_minutes=40)
        self.assertEqual(self.plan(hour_seconds=60, **lobby), ("lobby", 120))
        self.assertEqual(self.plan(hour_seconds=100, **lobby), ("lobby", 90))   # rounded up
        self.assertIsNone(self.plan(hour_seconds=60, lobby_done=self.now - 30,
                                    race_ended=self.now - 30, hour_minutes=40))
        self.assertIsNone(self.plan(hour_seconds=60, race_ended=self.now - 30, hour_minutes=20))
        # no race ads in the lobby, even with quiet chat
        self.assertIsNone(self.plan(race_ended=self.now - 30, hour_minutes=20))

    def test_snooze_when_chat_is_busy(self):
        due = {"next_ad_at": str(self.now + 60), "snooze_count": 2}
        self.assertTrue(tc.snooze_now(ADS, due, self.now, 5))
        self.assertFalse(tc.snooze_now(ADS, due, self.now, 0))           # quiet: let it run
        self.assertFalse(tc.snooze_now(ADS, dict(due, snooze_count=0), self.now, 5))
        self.assertFalse(tc.snooze_now(ADS, dict(due, next_ad_at=self.now + 900), self.now, 5))
        self.assertFalse(tc.snooze_now(dict(ADS, snooze_when_busy=False), due, self.now, 5))

    def test_ad_times(self):
        self.assertEqual(tc.stamp("2026-10-12T20:00:00+00:00"), 1791835200)
        self.assertEqual(tc.stamp("1791835200"), 1791835200)
        self.assertEqual(tc.stamp(""), 0)


if __name__ == "__main__":
    unittest.main()
