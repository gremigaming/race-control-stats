import unittest

from bot import twitch_chat as tc

CONFIG = {"only_live": True, "gap": 10, "min_chat": 0, "timers": [
    {"text": "discord", "first": 15, "every": 45},
    {"text": "site", "first": 30, "every": 60},
]}
ADS = {"auto": True, "length": 30, "max_length": 60, "race_length": 30, "target_per_hour": 180, "first": 10,
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
            self.assertTrue(t.get("every") or t["events"] or t["commands"], t.get("id"))


class AdTests(unittest.TestCase):
    now = 100_000

    def plan(self, **kw):
        args = dict(ads=ADS, now=self.now, live_minutes=60, last_race_ad=0, race_ended=0,
                    lobby_done=0, asked=False, recent_chat=0, hour_seconds=0, hour_minutes=15)
        args.update(kw)
        return tc.plan_ad(**args)

    def test_asked_runs_while_live_only(self):
        self.assertEqual(self.plan(asked=True, recent_chat=20), ("asked", 30))
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
        self.assertEqual(self.plan(hour_seconds=150, **lobby), ("lobby", 30))
        self.assertEqual(self.plan(hour_seconds=60, **lobby), ("lobby", 60))    # capped at 60 s
        self.assertEqual(self.plan(hour_seconds=140, **lobby), ("lobby", 60))   # rounded up
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



class ChatCommandTest(unittest.TestCase):
    def test_only_mods_and_broadcaster(self):
        config = {"timers": [{"text": "a", "commands": []},
                             {"text": "Simgasm!", "commands": ["!simgasm", "!setup"], "ask": r"\brig\b.*\?"},
                             {"text": "long", "ask_text": "short", "commands": ["!showname"]}]}
        self.assertEqual(tc.chat_match(config, "!simgasm"), (1, "Simgasm!"))
        self.assertEqual(tc.chat_match(config, "!setup please"), (1, "Simgasm!"))
        self.assertEqual(tc.chat_match(config, "nice rig?"), (1, "Simgasm!"))
        self.assertEqual(tc.chat_match(config, "!showname"), (2, "short"))
        self.assertIsNone(tc.chat_match(config, "simgasm"))
        self.assertIsNone(tc.chat_match(config, "!discord"))
        self.assertIsNone(tc.chat_match(config, ""))


if __name__ == "__main__":
    unittest.main()


class TitleTests(unittest.TestCase):
    def test_picks_unused_then_oldest(self):
        self.assertEqual(tc.pick_title(["a", "b", "c"], ["a"]), "b")
        self.assertEqual(tc.pick_title(["a", "b"], ["b", "a"]), "b")
        self.assertIsNone(tc.pick_title(["", " "], []))
        self.assertEqual(len(tc.pick_title(["x" * 200], [])), 140)

    def test_segment_window(self):
        now = 1_000_000
        seg = lambda i, mins, **kw: dict(id=i, start_time=str(int(now + mins * 60)), **kw)
        self.assertIsNone(tc.segment_to_title([seg("a", 90)], now, 60, []))
        self.assertEqual(tc.segment_to_title([seg("a", 30)], now, 60, [])["id"], "a")
        self.assertIsNone(tc.segment_to_title([seg("a", 30)], now, 60, ["a"]))
        self.assertEqual(tc.segment_to_title(
            [seg("a", 30, canceled_until="x"), seg("b", 50)], now, 60, [])["id"], "b")
        self.assertEqual(tc.segment_to_title([seg("a", -10)], now, 60, [])["id"], "a")
        self.assertIsNone(tc.segment_to_title([seg("a", -20)], now, 60, []))


class GameTimerTests(unittest.TestCase):
    def test_game_only_timer(self):
        config = {"only_live": True, "gap": 0, "min_chat": 0, "timers": [
            {"text": "names", "first": 0, "every": 30, "game": "F1"}]}
        self.assertIsNone(tc.next_timer(config, 10, {}, 0, 10_000, game="Le Mans Ultimate"))
        self.assertIsNone(tc.next_timer(config, 10, {}, 0, 10_000))
        self.assertEqual(tc.next_timer(config, 10, {}, 0, 10_000, game="EA SPORTS F1 25"), 0)


class EventTests(unittest.TestCase):
    config = {"timers": [
        {"id": "names", "game": "F1", "events": {"hidden_driver": 3}, "cooldown": 5},
        {"id": "discord", "events": {"new_chatter": 2, "stream_end": 0}, "cooldown": 15},
        {"id": "stats", "every": 40, "first": 0, "events": {}, "skip_after": {"id": "names", "minutes": 15}},
        {"id": "follow", "every": 45, "first": 0, "quiet": 10, "events": {}},
    ]}

    def test_event_waits_then_fires_once_per_cooldown(self):
        events, now = [], 100_000
        tc.queue_event(self.config, events, "new_chatter", now)
        tc.queue_event(self.config, events, "new_chatter", now + 30)
        self.assertIsNone(tc.due_event(self.config, events, {}, 0, now + 60))
        self.assertEqual(tc.due_event(self.config, events, {}, 0, now + 121), 1)
        sent = {1: now + 121}
        self.assertIsNone(tc.due_event(self.config, events, sent, now + 121, now + 300))
        self.assertEqual(events, [])  # the second one was dropped by the cooldown

    def test_event_respects_game_and_gap(self):
        events, now = [], 100_000
        tc.queue_event(self.config, events, "hidden_driver", now, game="Le Mans Ultimate")
        self.assertEqual(events, [])
        tc.queue_event(self.config, events, "hidden_driver", now, game="F1 25")
        self.assertIsNone(tc.due_event(self.config, events, {}, now + 170, now + 181, "F1 25"))
        self.assertEqual(tc.due_event(self.config, events, {}, now + 100, now + 181, "F1 25"), 0)

    def test_clock_rules(self):
        cfg = dict(self.config, only_live=True, gap=0, min_chat=0)
        now = 100_000
        self.assertIsNone(tc.next_timer(cfg, 50, {0: now - 600, 3: 0}, now - 60, now))  # names 10 min ago, follow not quiet
        self.assertEqual(tc.next_timer(cfg, 50, {0: now - 1000, 3: 0}, now - 60, now), 2)
        self.assertEqual(tc.next_timer(cfg, 50, {0: now - 600, 2: now - 700}, now - 700, now), 3)

    def test_hidden_keys(self):
        def car(name, num, nat, hidden=True, ai=False):
            return {"driver-name": name, "participant-data": {"race-number": num, "nationality": nat,
                    "platform": "Steam", "show-online-names": not hidden, "ai-controlled": ai}}
        data = {"classification-data": [car("RB #2", 2, "Unspecified"), car("X", 7, "Dutch", hidden=False),
                                        car("Ferrari #9", 9, "Polish"), car("AI", 4, "Dutch", ai=True)]}
        self.assertEqual(tc.hidden_keys(data), {"car:RB #2", "9|Polish|Steam"})

    def test_room_new_chatter_only_once(self):
        room = tc.Room()
        room.live, load = True, tc.load_timers
        tc.load_timers = lambda: self.config
        try:
            room.talked("tw:1"); room.talked("tw:1"); room.talked("tw:2")
        finally:
            tc.load_timers = load
        self.assertEqual(len(room.events), 2)


class QuestionTests(unittest.TestCase):
    def test_real_questions(self):
        config = tc.load_timers()
        ids = lambda q: (lambda h: config["timers"][h[0]]["id"] if h else None)(tc.chat_match(config, q.lower()))
        for q, want in [("what rig do you use?", "simgasm"), ("what pedals are those", "simgasm"),
                        ("is that a simgasm?", "simgasm"), ("what are you driving on", "simgasm"),
                        ("share your setup for spa pls", None), ("whats your car setup", None),
                        ("what setup for monza?", None), ("nice rig!", None), ("what is your name?", None),
                        ("hoe zet je je naam aan?", "names"), ("is there a discord?", "discord"),
                        ("where can i see my stats", "stats"), ("what are the f1 standings?", None),
                        ("how do i enter the giveaway", "giveaway"), ("is subbing worth it", "subs"),
                        ("thanks for the sub!", None)]:
            self.assertEqual(ids(q), want, q)
