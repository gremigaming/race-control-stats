"""Tests for bot/replies.py (no Discord or Claude needed)."""
import unittest

from bot import replies


class RepliesTests(unittest.TestCase):
    def test_only_owner_tags_are_answered(self):
        self.assertTrue(replies.should_answer(1, False, 1, True))
        self.assertFalse(replies.should_answer(2, False, 1, True))
        self.assertFalse(replies.should_answer(1, False, 1, False))
        self.assertFalse(replies.should_answer(1, True, 1, True))

    def test_history_is_quoted_as_data(self):
        msgs = replies.build_messages([("Bob", "hi"), ("Ann", "yo")], "how fast?", "Milan")
        text = msgs[0]["content"]
        self.assertIn("<chat_history>\nBob: hi\nAnn: yo\n</chat_history>", text)
        self.assertIn('<tagged_message from="Milan">\nhow fast?', text)

    def test_clean_reply(self):
        self.assertEqual(replies.clean_reply("Fast—really @everyone"), "Fast, really everyone")
        self.assertEqual(len(replies.clean_reply("x" * 5000)), replies.MAX_REPLY)
        self.assertTrue(replies.clean_reply(""))
