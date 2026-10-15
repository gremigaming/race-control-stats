import datetime
import unittest

from bot import scheduled_posts as sp
from bot.replies import TZ

POSTS = sp.load_config()
POST = POSTS[0]
AT = sp.post_time(POST)


class ScheduledPostTests(unittest.TestCase):
    def test_posts_once_at_the_time(self):
        self.assertEqual(sp.due(POSTS, {}, AT - datetime.timedelta(minutes=1)), [])
        self.assertEqual(sp.due(POSTS, {}, AT), [POST])
        self.assertEqual(sp.due(POSTS, {POST["key"]: 123}, AT), [])  # already posted

    def test_far_too_late_is_skipped(self):
        self.assertEqual(sp.due(POSTS, {}, AT + sp.LATE_LIMIT), [])

    def test_amsterdam_time(self):
        self.assertEqual(AT.tzinfo, TZ)

    def test_reaction_gives_the_role_only_on_that_message(self):
        state = {POST["key"]: 555}
        self.assertEqual(sp.role_for(POSTS, state, 555, POST["reaction"]), POST["reaction_role"])
        self.assertIsNone(sp.role_for(POSTS, state, 555, "👍"))
        self.assertIsNone(sp.role_for(POSTS, state, 556, POST["reaction"]))
        self.assertIsNone(sp.role_for(POSTS, {}, 555, POST["reaction"]))

    def test_text_pings_only_its_role(self):
        self.assertNotIn("@everyone", POST["text"])
        self.assertLess(len(POST["text"]), 2000)


if __name__ == "__main__":
    unittest.main()
