import unittest
import datetime as dt

import stream_log as S


def t(h, m=0, day=10):
    return dt.datetime(2026, 11, day, h, m, tzinfo=dt.timezone.utc)


class StreamLogTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(S.duration("3h2m1s"), 3 * 3600 + 121)
        self.assertEqual(S.duration("45s"), 45)

    def test_live_then_offline_with_vod(self):
        log = S.merge({}, t(18, 2), [(t(18, 2), t(18, 30))], t(18, 30))  # the live VOD still grows
        self.assertEqual(log["streams"], [{"start": "2026-11-10T18:02:00Z", "end": None}])
        self.assertEqual(log["since"], "2026-11-10T18:02:00Z")
        log = S.merge(log, None, [(t(18, 2), t(21, 10))], t(21, 20))
        self.assertEqual(log["streams"], [{"start": "2026-11-10T18:02:00Z", "end": "2026-11-10T21:10:00Z"}])

    def test_offline_without_vod_closes_at_now(self):
        log = S.merge({}, t(18), [], t(18, 5))
        log = S.merge(log, None, [], t(21, 7))
        self.assertEqual(log["streams"][0]["end"], "2026-11-10T21:07:00Z")

    def test_unchanged_when_nothing_new(self):
        log = S.merge({}, None, [(t(18), t(20))], t(22))
        self.assertEqual(S.merge(log, None, [(t(18), t(20))], t(23)), log)

    def test_old_vods_fill_history_and_since_stays(self):
        log = S.merge({}, None, [(t(18), t(20))], t(22))
        log = S.merge(log, None, [(t(18), t(20)), (t(19, day=8), t(22, day=8))], t(22))
        self.assertEqual(len(log["streams"]), 2)
        self.assertEqual(log["since"], "2026-11-10T18:00:00Z")

    def test_false_start_does_not_cut_the_real_stream(self):
        # 20 Nov 2026: a short broadcast ended 14:07, the real one ran 14:10-17:00 (Twitch lists newest first)
        vods = [(t(14, 10, day=20), t(17, day=20)), (t(13, 58, day=20), t(14, 7, day=20))]
        log = S.merge({}, None, vods, t(17, 15, day=20))
        self.assertEqual(log["streams"], [{"start": "2026-11-20T13:58:00Z", "end": "2026-11-20T17:00:00Z"}])

    def test_broken_window_is_repaired(self):
        broken = {"since": "2026-11-10T18:00:00Z",
                  "streams": [{"start": "2026-11-20T14:10:25Z", "end": "2026-11-20T14:07:28Z"}]}
        vods = [(t(14, 10, day=20), t(17, day=20)), (t(13, 58, day=20), t(14, 7, day=20))]
        log = S.merge(broken, None, vods, t(23, day=20))
        self.assertEqual(log["streams"], [{"start": "2026-11-20T13:58:00Z", "end": "2026-11-20T17:00:00Z"}])


if __name__ == "__main__":
    unittest.main()
