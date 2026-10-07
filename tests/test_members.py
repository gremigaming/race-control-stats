"""Tests for bot/members.py (no Discord needed)."""
import tempfile
import unittest
from pathlib import Path

from bot import members

NOW = 1_791_000_000  # 2026-10-03 UTC


class MembersTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "members.json"
        self.m = members.Members(self.path, Path(self.dir.name) / "messages")

    def tearDown(self):
        self.dir.cleanup()

    def test_counts_messages_channels_and_hours(self):
        for _ in range(3):
            self.m.message(1, "f1-chat", NOW)
        self.m.message(1, "general", NOW)
        line = self.m.profile(1, ["F1", "Member"], "2025-03-01", now=NOW)
        self.assertIn("4 messages", line)
        self.assertIn("4 this week", line)
        self.assertIn("most in f1-chat (3), general (1)", line)
        self.assertIn("roles F1, Member", line)
        self.assertIn("joined 2025-03-01", line)

    def test_voice_minutes(self):
        self.m.voice(1, True, NOW)
        self.m.voice(1, False, NOW + 1800)
        self.assertIn("30 min in voice", self.m.profile(1, now=NOW))

    def test_leaving_voice_without_joining_is_ignored(self):
        self.m.voice(2, False, NOW)
        self.assertEqual(self.m.profile(2), "no activity recorded yet")

    def test_old_days_are_dropped_but_total_kept(self):
        self.m.message(1, "general", NOW - 90 * 86400)
        self.m.message(1, "general", NOW)
        self.assertEqual(len(self.m.data["1"]["days"]), 1)
        self.assertEqual(self.m.data["1"]["messages"], 2)

    def test_saved_and_loaded(self):
        self.m.message(1, "general", NOW)
        self.m.maybe_save(force=True)
        self.assertIn("1 messages", members.Members(self.path).profile(1, now=NOW))

    def test_archive_and_forget(self):
        self.m.archive(10, 1, "general", "hello", NOW)
        self.m.archive(11, 2, "general", "hi", NOW)
        self.m.message(1, "general", NOW)
        self.assertEqual(self.m.forget(1), 1)
        rows = (Path(self.dir.name) / "messages" / "2026-10-03.jsonl").read_text()
        self.assertNotIn("hello", rows)
        self.assertIn("hi", rows)
        self.assertEqual(self.m.profile(1, now=NOW), "no activity recorded yet")

    def test_old_archive_files_are_dropped(self):
        self.m.archive(1, 1, "general", "old", NOW - 100 * 86400)
        self.m.archive(2, 1, "general", "new", NOW)
        files = sorted(p.name for p in (Path(self.dir.name) / "messages").iterdir())
        self.assertEqual(files, ["2026-10-03.jsonl"])

    def test_top_last_week(self):
        for _ in range(3):
            self.m.message(1, "general", NOW)
        self.m.message(2, "general", NOW)
        self.m.message(3, "general", NOW - 30 * 86400)
        self.assertEqual(self.m.top(5, now=NOW), [("1", 3), ("2", 1)])

    def test_profile_text_and_due_list(self):
        self.m.archive(1, 1, "f1-chat", "Max is the GOAT", NOW)
        self.m.archive(2, 2, "general", "hi", NOW)
        self.m.message(1, "f1-chat", NOW)
        self.assertEqual(self.m.said_since(1, 0), "[f1-chat] Max is the GOAT")
        self.assertEqual(self.m.due_for_summary(), ["1"])
        self.m.set_summary(1, "Verstappen fan who lives in F1 chat.", at=NOW + 1)
        self.assertEqual(self.m.due_for_summary(), [])
        self.assertIn("profile: Verstappen fan", self.m.profile(1, now=NOW))


class NamesTests(unittest.TestCase):
    def test_finds_members_by_loose_name(self):
        class M:
            def __init__(self, display_name, name):
                self.display_name, self.name = display_name, name
        hidde, shw, dd = M("TTV_H1ddegam1ng", "h1dde"), M("[QDR] Shw1ks", "shwiks"), M("DevilDriver", "dd")
        people = [hidde, shw, dd]
        self.assertEqual(members.named_members("Tell me something about hiddegaming", people), [hidde])
        self.assertEqual(members.named_members("how active is shwiks", people), [shw])
        self.assertEqual(members.named_members("what about hidde?", people), [hidde])
        self.assertEqual(members.named_members("how active am I", people), [])


if __name__ == "__main__":
    unittest.main()
