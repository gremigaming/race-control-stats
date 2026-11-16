import json
import unittest

import announce_short as a
from bot import scheduled_posts as sp


class AnnounceShortTests(unittest.TestCase):
    def test_light_post_without_ping(self):
        p = a.short_post("TITLE | F1 26", "https://www.youtube.com/watch?v=-evsL1E-OrI", "2026-11-17 19:00")
        self.assertEqual(p["key"], "short--evsL1E-OrI")
        self.assertNotIn("ping_role", p)
        self.assertNotIn("<@&", p["text"])
        self.assertIn("https://www.youtube.com/shorts/-evsL1E-OrI", p["text"])
        self.assertIn("<https://www.tiktok.com/@ttv.gremi_gaming>", p["text"])  # no second preview
        self.assertEqual(sp.post_time(p).hour, 19)

    def test_same_short_replaces_its_post(self):
        cfg = {"posts": [{"key": "x"}, {"key": "short-abcdefghijk", "post_at": "old"}]}
        p = a.short_post("T", "https://youtu.be/abcdefghijk", "2026-11-18 18:00")
        out = a.add(cfg, p)
        self.assertEqual([q["key"] for q in out["posts"]], ["x", "short-abcdefghijk"])
        self.assertEqual(out["posts"][1]["post_at"], "2026-11-18 18:00")

    def test_bad_input_is_refused(self):
        with self.assertRaises(SystemExit):
            a.short_post("T", "https://example.com", "2026-11-18 18:00")
        with self.assertRaises(ValueError):
            a.short_post("T", "https://youtu.be/abcdefghijk", "18 Nov 18:00")

    def test_config_keys_are_unique(self):
        keys = [p["key"] for p in json.loads(a.CONFIG.read_text(encoding="utf-8"))["posts"]]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
