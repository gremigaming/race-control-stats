import unittest

from bot import leaves as l


class LeaveTests(unittest.TestCase):
    def test_how_long(self):
        self.assertEqual(l.how_long(20), "less than a minute")
        self.assertEqual(l.how_long(3 * 60), "3 minutes")
        self.assertEqual(l.how_long(3600), "1 hour")
        self.assertEqual(l.how_long(40 * 86400), "1 month")
        self.assertEqual(l.how_long(800 * 86400), "2 years")

    def test_left_on_their_own(self):
        text = l.leave_text("left", "Speedy", "speedy23", 42, 5 * 86400, ["Member", "F1"])
        self.assertIn("🚪 **Speedy** (@speedy23, `42`) left the server on their own", text)
        self.assertIn("Was here 5 days", text)
        self.assertIn("Roles: Member, F1", text)
        self.assertNotIn("Reason", text)

    def test_kick_and_ban(self):
        kick = l.leave_text("kick", "Speedy", "speedy23", 42, by="<@7>", reason="spam")
        self.assertIn("was kicked by <@7>", kick)
        self.assertIn("Reason: spam", kick)
        self.assertIn("was banned", l.leave_text("ban", "Speedy", "speedy23", 42))


if __name__ == "__main__":
    unittest.main()
