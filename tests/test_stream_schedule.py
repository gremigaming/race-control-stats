"""Tests for stream_schedule.py and schedule_image.py (no network)."""
import contextlib
import datetime
import io
import os
import types
import unittest
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import schedule_image  # noqa: E402
import stream_schedule as ss  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 7, 13, 0, tzinfo=UTC)  # Wednesday
LOGIN = "gremi_gaming"
ME = "900"
SIGNUPBOT = "800"


def seg(id, day, hour, title="F1 26 LOBBIES ∣ RANDOM GRID", cancelled=False):
    start = datetime.datetime(2026, 10, day, hour, tzinfo=UTC)
    return {"id": id, "start": start, "end": start + datetime.timedelta(hours=2),
            "title": title, "category": "F1 25", "cancelled": cancelled}


def event(id, s, creator=SIGNUPBOT, name=None, location="https://twitch.tv/gremi_gaming"):
    return {"id": id, "name": name or s["title"], "creator_id": creator,
            "entity_type": 3, "status": 1, "entity_metadata": {"location": location},
            "scheduled_start_time": ss.iso(s["start"]),
            "scheduled_end_time": ss.iso(s["end"]),
            "description": ss.event_body(s, LOGIN)["description"]}


def kinds(actions):
    return sorted((a[0], a[-1]["id"] if a[0] == "create" else a[1]["id"]) for a in actions)


class PlanEvents(unittest.TestCase):
    def test_creates_missing_and_keeps_matching(self):
        a, b = seg("a", 7, 17), seg("b", 9, 17)
        actions = ss.plan_events([event("1", a)], [a, b], LOGIN, ME, NOW)
        self.assertEqual(kinds(actions), [("create", "b")])

    def test_moved_stream_is_edited_not_recreated(self):
        old, new = seg("a", 9, 13), seg("a", 9, 17)
        actions = ss.plan_events([event("1", old)], [new], LOGIN, ME, NOW)
        self.assertEqual(actions[0][0], "edit")
        self.assertEqual(list(actions[0][3]), ["scheduled_start_time", "scheduled_end_time"])

    def test_renamed_stream_is_edited(self):
        old = seg("a", 9, 17)
        new = seg("a", 9, 17, title="LMU daily races")
        actions = ss.plan_events([event("1", old)], [new], LOGIN, ME, NOW)
        self.assertEqual(actions[0][0], "edit")
        self.assertEqual(actions[0][3]["name"], "LMU daily races")

    def test_removed_and_cancelled_streams_are_deleted(self):
        a = seg("a", 9, 17)
        c = seg("c", 11, 17, cancelled=True)
        actions = ss.plan_events([event("1", a), event("2", c)], [c], LOGIN, ME, NOW)
        self.assertEqual(kinds(actions), [("delete", "1"), ("delete", "2")])

    def test_duplicate_keeps_signupbot_event(self):
        a = seg("a", 9, 17)
        evs = [event("5", a, creator=ME), event("6", a, creator=SIGNUPBOT)]
        actions = ss.plan_events(evs, [a], LOGIN, ME, NOW)
        self.assertEqual(kinds(actions), [("delete", "5")])

    def test_leaves_other_and_started_events_alone(self):
        a = seg("a", 9, 17)
        other = event("7", a, location="https://example.com")
        started = event("8", seg("x", 7, 12))
        started["status"] = 2
        past = event("9", seg("y", 7, 11))
        actions = ss.plan_events([other, started, past], [], LOGIN, ME, NOW)
        self.assertEqual(actions, [])

    def test_ignores_streams_past_the_week_window(self):
        far = seg("f", 20, 17)
        self.assertEqual(ss.plan_events([], [far], LOGIN, ME, NOW), [])


class Picture(unittest.TestCase):
    def test_week_and_labels(self):
        monday, start = ss.week_start(NOW)
        self.assertEqual(monday, datetime.date(2026, 10, 5))
        self.assertEqual(start.utcoffset(), datetime.timedelta(hours=2))
        self.assertEqual(ss.week_label(monday), "5 - 11 Oct")
        self.assertEqual(ss.week_label(datetime.date(2026, 10, 26)), "26 Oct - 1 Nov")
        self.assertEqual(ss.tz_label(NOW), "GMT+2")
        self.assertEqual(ss.tz_label(datetime.datetime(2026, 12, 1, tzinfo=UTC)), "GMT+1")

    def test_days_use_local_time(self):
        days = ss.week_days(datetime.date(2026, 10, 5), [seg("a", 7, 17)], None)
        self.assertEqual(days[2][1][0]["time"], "19:00")
        self.assertEqual([len(s) for _, s in days], [0, 0, 1, 0, 0, 0, 0])

    def test_vacation_marks_empty_days(self):
        vac = (datetime.datetime(2026, 10, 8, tzinfo=UTC), datetime.datetime(2026, 10, 10, tzinfo=UTC))
        days = ss.week_days(datetime.date(2026, 10, 5), [], vac)
        self.assertEqual([bool(s) for _, s in days], [False, False, False, True, True, False, False])

    def test_render_is_a_png(self):
        days = ss.week_days(datetime.date(2026, 10, 5),
                            [seg("a", 7, 17), seg("b", 10, 13), seg("c", 10, 19, "Night"),
                             seg("d", 11, 17, cancelled=True)], None)
        data = schedule_image.render(days, datetime.date(2026, 10, 7), "GMT+2",
                                     "GreMi_Gaming", "5 - 11 Oct")
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_clean_title(self):
        self.assertEqual(schedule_image.clean("F1 26 lobbies ∣ random grid \U0001F3CE"),
                         "F1 26 LOBBIES | RANDOM GRID")


class FakeStats:
    TWITCH_LOGIN = LOGIN
    GUILD_ID = "111"

    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def discord(self, method, path, data=None):
        self.calls.append((method, path))
        if path == "/users/@me":
            return {"id": ME}
        if "/messages" in path:
            return self.messages
        return []


class UpdatePicture(unittest.TestCase):
    def run_update(self, messages, stats=None):
        stats = stats or FakeStats(messages)
        sent = []
        with mock.patch.object(ss, "SCHEDULE_CHANNEL_ID", "55"), \
                mock.patch.object(ss, "NOTIFY_ROLE_ID", "77"), \
                mock.patch("socials_board.send",
                           side_effect=lambda st, m, p, msg, files: sent.append((m, p, msg)) or {"id": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            ss.update_picture(stats, [seg("a", 7, 17)], None, NOW)
        return sent

    def test_first_post_is_silent(self):
        sent = self.run_update([])
        self.assertEqual(sent[0][0], "POST")
        self.assertNotIn("<@&", sent[0][2]["content"])

    def test_new_week_pings_notify_me(self):
        old = {"id": "3", "author": {"id": ME}, "content": "x",
               "attachments": [{"filename": "stream-schedule-2026-09-28-abc.png"}]}
        sent = self.run_update([old])
        self.assertEqual(sent[0][0], "POST")
        self.assertIn("<@&77>", sent[0][2]["content"])
        self.assertEqual(sent[0][2]["allowed_mentions"], {"roles": ["77"]})

    def test_new_week_deletes_old_posts(self):
        old = {"id": "3", "author": {"id": ME}, "content": "x",
               "attachments": [{"filename": "stream-schedule-2026-09-28-abc.png"}]}
        stats = FakeStats([old])
        self.run_update([old], stats)
        self.assertIn(("DELETE", "/channels/55/messages/3"), stats.calls)

    def test_changed_week_is_edited_and_unchanged_is_left(self):
        post = {"id": "4", "author": {"id": ME}, "content": "kept",
                "attachments": [{"filename": "stream-schedule-2026-10-05-old.png"}]}
        old = {"id": "2", "author": {"id": ME}, "content": "x",
               "attachments": [{"filename": "stream-schedule-2026-09-28-abc.png"}]}
        stats = FakeStats([post, old])
        sent = self.run_update(None, stats)
        self.assertEqual(sent[0][:2], ("PATCH", "/channels/55/messages/4"))
        self.assertIn(("DELETE", "/channels/55/messages/2"), stats.calls)
        self.assertNotIn(("DELETE", "/channels/55/messages/4"), stats.calls)
        self.assertEqual(sent[0][2]["content"], "kept")
        name = sent[0][2]["attachments"][0]["filename"]
        post["attachments"] = [{"filename": name}]
        self.assertEqual(self.run_update([post]), [])


class ApprovalStats(FakeStats):
    """Schedule channel holds `posts`; the DM channel "dm" holds `dms`."""
    def __init__(self, posts, dms, approvers=()):
        super().__init__(posts)
        self.dms, self.approvers = dms, approvers

    def discord(self, method, path, data=None):
        self.calls.append((method, path))
        if path == "/users/@me/channels":
            return {"id": "dm"}
        if path == "/users/@me":
            return {"id": ME}
        if path.startswith("/channels/dm/messages?"):
            return self.dms
        if "/reactions/" in path and method == "GET":
            return [{"id": a} for a in self.approvers]
        if "/messages" in path:
            return self.messages
        return []


class Approval(unittest.TestCase):
    OLD = {"id": "3", "author": {"id": ME}, "content": "x",
           "attachments": [{"filename": "stream-schedule-2026-09-28-abc.png"}]}

    def run_update(self, stats, now=NOW):
        sent = []
        with mock.patch.object(ss, "SCHEDULE_CHANNEL_ID", "55"), \
                mock.patch.object(ss, "NOTIFY_ROLE_ID", "77"), \
                mock.patch.object(ss, "APPROVER_ID", "42"), \
                mock.patch("socials_board.send",
                           side_effect=lambda st, m, p, msg, files: sent.append((m, p, msg)) or {"id": "9"}), \
                contextlib.redirect_stdout(io.StringIO()):
            ss.update_picture(stats, [seg("a", 7, 17)], None, now)
        return sent

    def approve_name(self):
        days = ss.week_days(ss.week_start(NOW)[0], [seg("a", 7, 17)], None)
        return f"stream-schedule-2026-10-05-approve-{ss.schedule_key(days)}.png"

    def dm(self, name):
        return {"id": "8", "author": {"id": ME}, "content": "first line\nmore",
                "attachments": [{"filename": name}]}

    def test_new_week_is_dmed_first_not_posted(self):
        stats = ApprovalStats([self.OLD], [])
        sent = self.run_update(stats)
        self.assertEqual([(m, p) for m, p, _ in sent], [("POST", "/channels/dm/messages")])
        self.assertEqual(sent[0][2]["attachments"][0]["filename"], self.approve_name())
        self.assertIn(("PUT", "/channels/dm/messages/9/reactions/%E2%9C%85/@me"), stats.calls)
        self.assertNotIn(("DELETE", "/channels/55/messages/3"), stats.calls)

    def test_no_dm_at_night(self):
        night = datetime.datetime(2026, 10, 5, 5, 0, tzinfo=UTC)  # Monday 07:00 Amsterdam
        stats = ApprovalStats([self.OLD], [])
        self.assertEqual(self.run_update(stats, night), [])
        self.assertNotIn(("POST", "/users/@me/channels"), stats.calls)

    def test_pending_sends_nothing(self):
        stats = ApprovalStats([self.OLD], [self.dm(self.approve_name())], approvers=[ME])
        self.assertEqual(self.run_update(stats), [])

    def test_approved_posts_with_ping(self):
        stats = ApprovalStats([self.OLD], [self.dm(self.approve_name())], approvers=[ME, "42"])
        sent = self.run_update(stats)
        self.assertEqual(sent[0][:2], ("POST", "/channels/55/messages"))
        self.assertIn("<@&77>", sent[0][2]["content"])

    def test_changed_schedule_sends_new_dm_and_marks_old(self):
        stats = ApprovalStats([self.OLD], [self.dm("stream-schedule-2026-10-05-approve-old.png")],
                              approvers=[ME, "42"])
        sent = self.run_update(stats)
        self.assertEqual(sent[0][:2], ("POST", "/channels/dm/messages"))
        self.assertIn(("PATCH", "/channels/dm/messages/8"), stats.calls)

    def test_edits_after_posting_need_no_approval(self):
        post = {"id": "4", "author": {"id": ME}, "content": "kept",
                "attachments": [{"filename": "stream-schedule-2026-10-05-old.png"}]}
        stats = ApprovalStats([post], [])
        sent = self.run_update(stats)
        self.assertEqual(sent[0][:2], ("PATCH", "/channels/55/messages/4"))


if __name__ == "__main__":
    unittest.main()
