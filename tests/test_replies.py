"""Tests for bot/replies.py (no Discord needed)."""
import json
import unittest

from bot import replies

OWNER, MOD, MEMBER, BOT = 1, 2, 3, 9
MODS = {MOD}


class RepliesTests(unittest.TestCase):
    def test_staff_file_loads(self):
        owner, mods = replies.load_staff()
        self.assertEqual(owner, 319544045435224067)
        self.assertTrue(mods)

    def test_owner_and_listed_moderators_are_handled(self):
        self.assertTrue(replies.should_handle_tag(OWNER, False, True, OWNER, MODS))
        self.assertTrue(replies.should_handle_tag(MOD, False, True, OWNER, MODS))

    def test_others_bots_and_untagged_are_ignored(self):
        self.assertFalse(replies.should_handle_tag(MEMBER, False, True, OWNER, MODS))
        self.assertFalse(replies.should_handle_tag(OWNER, False, False, OWNER, MODS))
        self.assertFalse(replies.should_handle_tag(MOD, True, True, OWNER, MODS))

    def test_only_owner_checkmark_on_bot_plan_approves(self):
        plan = replies.PLAN_MARK + ": rename general"
        ok = replies.is_approval(replies.APPROVE, OWNER, OWNER, BOT, BOT, plan)
        self.assertTrue(ok)
        self.assertFalse(replies.is_approval(replies.APPROVE, MOD, OWNER, BOT, BOT, plan))
        self.assertFalse(replies.is_approval("\U0001F44D", OWNER, OWNER, BOT, BOT, plan))
        self.assertFalse(replies.is_approval(replies.APPROVE, OWNER, OWNER, MEMBER, BOT, plan))
        self.assertFalse(replies.is_approval(replies.APPROVE, OWNER, OWNER, BOT, BOT, "hi"))

    def test_bodies_carry_only_ids(self):
        tag = json.loads(replies.tag_body(10, 20, 40))["text"]
        self.assertIn("channel=10 message=20 author=40", tag)
        ok = json.loads(replies.approval_body(10, 50, 1))["text"]
        self.assertIn("channel=10 plan=50 approver=1", ok)


class PlainNameTests(unittest.TestCase):
    def test_fancy_names_become_plain(self):
        self.assertEqual(replies.plain("\U0001F5D3\uFE0F\u2503\U0001D5E6\U0001D5E7\U0001D5E5\U0001D5D8\U0001D5D4\U0001D5E0-\U0001D5E6\U0001D5D6\U0001D5DB\U0001D5D8\U0001D5D7\U0001D5E8\U0001D5DF\U0001D5D8"),
                         "stream-schedule")
        self.assertEqual(replies.plain("\U0001F7E3\u2503\U0001D5E7\U0001D5EA\U0001D5DC\U0001D5E7\U0001D5D6\U0001D5DB: \U0001D7ED,\U0001D7F2\U0001D7EF\U0001D7F5"),
                         "twitch 1639")


class AccessTests(unittest.TestCase):
    def test_staff_check(self):
        self.assertTrue(replies.is_staff(OWNER, OWNER, MODS))
        self.assertTrue(replies.is_staff(MOD, OWNER, MODS))
        self.assertFalse(replies.is_staff(MEMBER, OWNER, MODS))

    def test_member_cooldown(self):
        c = replies.Cooldown()
        self.assertTrue(c.allow(MEMBER, 1000))
        self.assertFalse(c.allow(MEMBER, 1010))
        self.assertTrue(c.allow(MEMBER, 1030))
        self.assertTrue(c.allow(OWNER, 1010))
        for i in range(replies.Cooldown.PER_DAY - 2):
            self.assertTrue(c.allow(MEMBER, 2000 + 30 * i))
        self.assertFalse(c.allow(MEMBER, 40000))
        self.assertTrue(c.allow(MEMBER, 86400 * 2))
