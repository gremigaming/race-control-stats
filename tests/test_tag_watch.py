"""Tests for tag_watch.py using simulated API responses (no network)."""
import os
import unittest
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import tag_watch  # noqa: E402

NOW = 1_800_000_000.0
BOT, OWNER, OTHER, BOT_ROLE = "42", "555", "777", "88"


def sid(seconds_ago):
    return str(int((NOW - seconds_ago) * 1000 - tag_watch.DISCORD_EPOCH_MS) << 22)


class FakeDiscord:
    def __init__(self, msgs, last=60):
        self.msgs = msgs
        self.last = last
        self.calls = []

    def __call__(self, method, path, data=None):
        self.calls.append((method, path))
        if path == "/users/@me":
            return {"id": BOT}
        if path == "/guilds/111":
            return {"owner_id": OWNER}
        if path.endswith("/roles"):
            return [{"id": BOT_ROLE, "tags": {"bot_id": BOT}}, {"id": "1"}]
        if path.endswith("/threads/active"):
            return {"threads": []}
        if path.endswith("/channels"):
            return [{"id": "10", "type": 0, "last_message_id": sid(self.last)},
                    {"id": "11", "type": 0, "last_message_id": sid(7200)},
                    {"id": "12", "type": 2}]
        if "/messages?" in path:
            return self.msgs
        return {}


def msg(mid, author=OWNER, mentions=(BOT,), roles=(), **extra):
    return {"id": mid, "author": {"id": author},
            "mentions": [{"id": u} for u in mentions],
            "mention_roles": list(roles), **extra}


class TagWatchTests(unittest.TestCase):
    def find(self, msgs, last=60):
        fake = FakeDiscord(msgs, last)
        with mock.patch.object(tag_watch, "discord", fake):
            return tag_watch.find_new_tags(NOW), fake

    def test_owner_tag_is_found_only_in_recent_channels(self):
        m = sid(60)
        tags, fake = self.find([msg(m)])
        self.assertEqual(tags, [("10", m)])
        read = [p for _, p in fake.calls if "/messages?" in p]
        self.assertEqual(read, ["/channels/10/messages?limit=25"])

    def test_role_tag_counts(self):
        m = sid(60)
        tags, _ = self.find([msg(m, mentions=(), roles=(BOT_ROLE,))])
        self.assertEqual(tags, [("10", m)])

    def test_others_old_and_untagged_messages_are_ignored(self):
        tags, _ = self.find([msg(sid(60), author=OTHER),
                             msg(sid(60), mentions=()),
                             msg(sid(3600))])
        self.assertEqual(tags, [])

    def test_already_handled_tags_are_skipped(self):
        replied, eyes = sid(120), sid(60)
        tags, _ = self.find([
            msg(sid(30), author=BOT, mentions=(),
                message_reference={"message_id": replied}),
            msg(eyes, reactions=[{"me": True, "emoji": {"name": tag_watch.EYES}}]),
            msg(replied),
        ])
        self.assertEqual(tags, [])

    def test_main_wakes_claude_then_marks(self):
        order = []
        with mock.patch.object(tag_watch, "ROUTINE_ID", "trig_1"), \
                mock.patch.object(tag_watch, "ROUTINE_TOKEN", "tok"), \
                mock.patch.object(tag_watch, "find_new_tags", return_value=[("10", "5")]), \
                mock.patch.object(tag_watch, "http", side_effect=lambda *a: order.append(a)), \
                mock.patch.object(tag_watch, "discord",
                                  side_effect=lambda *a: order.append(a)):
            tag_watch.main()
        self.assertEqual(order[0][1], tag_watch.FIRE_URL.format("trig_1"))
        self.assertEqual(order[0][2]["Authorization"], "Bearer tok")
        self.assertIn("10 / 5", order[0][3]["text"])
        self.assertEqual(order[1][0], "PUT")
        self.assertIn("/channels/10/messages/5/reactions/", order[1][1])

    def test_failed_wake_leaves_tag_unmarked(self):
        disc = mock.Mock()
        with mock.patch.object(tag_watch, "ROUTINE_ID", "trig_1"), \
                mock.patch.object(tag_watch, "ROUTINE_TOKEN", "tok"), \
                mock.patch.object(tag_watch, "find_new_tags", return_value=[("10", "5")]), \
                mock.patch.object(tag_watch, "http", side_effect=RuntimeError("boom")), \
                mock.patch.object(tag_watch, "discord", disc):
            with self.assertRaises(RuntimeError):
                tag_watch.main()
        disc.assert_not_called()

    def test_missing_token_does_nothing(self):
        with mock.patch.object(tag_watch, "ROUTINE_TOKEN", ""), \
                mock.patch.object(tag_watch, "find_new_tags") as find:
            tag_watch.main()
        find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
