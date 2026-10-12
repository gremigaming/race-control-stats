"""Tests for setups_post.py (no network)."""
import contextlib
import io
import json
import os
import types
import unittest
from unittest import mock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-discord-token")
os.environ.setdefault("GUILD_ID", "111")

import setups_post as sp  # noqa: E402

ME = "900"


def card(kind, h, **extra):
    return {"kind": kind, "picture": f"setup-japan-{kind}.png", "hash": h, **extra}


TRACK = {"post": "Japan", "cards": [
    card("overview", "o1", circuit="Suzuka"),
    card("race", "r1", label="Dry race", date="Used in the race on 2 Oct 2026"),
    card("quali", "q1", label="Dry quali", date="Used in the qualifying on 2 Oct 2026"),
    card("wet", "w1", label="Wet", placeholder=True),
]}


class Messages(unittest.TestCase):
    def test_opening_lists_all_three_kinds(self):
        name, msg = sp.opening(TRACK, TRACK["cards"][0], TRACK["cards"][1:])
        texts = json.dumps(msg["components"], ensure_ascii=False)
        self.assertIn("🇯🇵 Japan", texts)
        self.assertIn("Suzuka", texts)
        self.assertIn("**Dry race** ─ 📅 2 Oct 2026", texts)
        self.assertIn("**Wet** ─ ⏳ no setup yet", texts)
        self.assertEqual(msg["flags"], sp.COMPONENTS_V2)
        self.assertTrue(name.startswith("setup-japan-overview-"))

    def test_quali_same_as_race(self):
        cards = [TRACK["cards"][1], TRACK["cards"][3]]
        _, msg = sp.opening(TRACK, TRACK["cards"][0], cards)
        self.assertIn("same as the dry race setup", json.dumps(msg["components"], ensure_ascii=False))

    def test_placeholder_card_is_gray(self):
        _, msg = sp.card_message(TRACK["cards"][3])
        self.assertEqual(msg["components"][0]["accent_color"], sp.GRAY)
        self.assertIn("No setup yet", msg["components"][0]["components"][0]["content"])


class Update(unittest.TestCase):
    def run_update(self, threads, messages):
        calls, sent = [], []
        stats = types.SimpleNamespace(UA="test", GUILD_ID="111")

        def discord(method, path, data=None):
            calls.append((method, path, data))
            if path == "/users/@me":
                return {"id": ME}
            if path.endswith("/threads/active"):
                return {"threads": []}
            if "/threads/archived/" in path:
                return {"threads": threads, "has_more": False}
            if path == "/channels/42":
                return {"available_tags": [{"id": "t1", "name": "Japan"}]}
            return messages

        stats.discord = discord

        def fetch(_stats, path):
            return json.dumps({"tracks": [TRACK]}).encode() if path.startswith("discord/setups.json") else b"png"

        def send(_stats, method, path, message, files):
            sent.append((method, path, message, files))
            return {"id": "new"}

        with mock.patch.object(sp, "SETUPS_FORUM_ID", "42"), \
                mock.patch("leaderboard_post.fetch", fetch), \
                mock.patch("socials_board.send", send), \
                contextlib.redirect_stdout(io.StringIO()):
            sp.update(stats)
        return calls, sent

    def test_new_post_overview_first_then_cards(self):
        _, sent = self.run_update([{"id": "5", "name": "Japan", "owner_id": "someone else"}], [])
        self.assertEqual(sent[0][:2], ("POST", "/channels/42/threads"))
        self.assertEqual(sent[0][2]["applied_tags"], ["t1"])
        self.assertTrue(sent[0][3][0][0].startswith("setup-japan-overview-"))
        self.assertEqual([s[1] for s in sent[1:]], ["/channels/new/messages"] * 3)

    def existing(self):
        msgs = []
        for c in TRACK["cards"]:
            if c["kind"] == "overview":
                name = sp.opening(TRACK, c, TRACK["cards"][1:])[0]
                mid = "55"
            else:
                name = sp.card_message(c)[0]
                mid = "m-" + c["kind"]
            url = f"https://cdn.discordapp.com/attachments/1/2/{name}?ex=1"
            msgs.append({"id": mid, "author": {"id": ME}, "attachments": [], "components": [
                {"type": 17, "components": [{"type": 12, "items": [{"media": {"url": url}}]}]}]})
        return msgs

    def test_unchanged_sends_nothing(self):
        _, sent = self.run_update([{"id": "55", "name": "Japan", "owner_id": ME}], self.existing())
        self.assertEqual(sent, [])

    def test_changed_card_edits_only_that_message(self):
        msgs = self.existing()
        msgs[1]["components"][0]["components"][0]["items"][0]["media"]["url"] = "x/setup-japan-race-old.png"
        _, sent = self.run_update([{"id": "55", "name": "Japan", "owner_id": ME}], msgs)
        self.assertEqual([s[:2] for s in sent], [("PATCH", "/channels/55/messages/m-race")])

    def test_off_without_forum_id(self):
        with mock.patch.object(sp, "SETUPS_FORUM_ID", ""):
            self.assertIsNone(sp.update(types.SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
