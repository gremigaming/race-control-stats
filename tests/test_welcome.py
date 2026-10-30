import unittest

from bot import welcome as w

CONFIG = w.load_config()
NOW = 1791990000


class WelcomeTests(unittest.TestCase):
    def test_first_join_starts_the_hour(self):
        s = w.fresh()
        w.add(s, 1, NOW, 3600)
        w.add(s, 2, NOW + 1200, 3600)  # joins later, same message
        self.assertEqual(s, {"due": NOW + 3600, "ids": [1, 2]})
        self.assertFalse(w.is_due(s, NOW + 3599))
        self.assertTrue(w.is_due(s, NOW + 3600))

    def test_typing_sends_it_right_away(self):
        s = w.fresh()
        w.add(s, 1, NOW, 3600)
        w.add(s, 2, NOW, 3600)
        self.assertFalse(w.is_due(s, NOW + 60, typed_by=3))  # someone else typed
        self.assertTrue(w.is_due(s, NOW + 60, typed_by=2))

    def test_leaving_drops_them(self):
        s = w.fresh()
        w.add(s, 1, NOW, 3600)
        w.add(s, 2, NOW, 3600)
        w.remove(s, 1)
        self.assertEqual(s["ids"], [2])
        w.remove(s, 2)
        self.assertEqual(s, w.fresh())
        self.assertFalse(w.is_due(s, NOW + 9999))

    def test_questions_rotate(self):
        qs = CONFIG["questions"]
        self.assertGreaterEqual(len(qs), 10)
        asked, seen = [], []
        for _ in range(200):
            q, asked = w.pick_question(qs, asked)
            self.assertNotIn(q, seen[-(len(qs) // 2):])  # none of the last few again
            seen.append(q)
        self.assertEqual(set(seen), set(qs))
        self.assertIn("Say hi and " + qs[1], w.general_text([1], CONFIG, qs[1]))

    def test_general_text(self):
        self.assertIn("<@1>, the newest driver ", w.general_text([1], CONFIG))
        self.assertIn("<@1>, <@2> and <@3>, the newest drivers", w.general_text([1, 2, 3], CONFIG))

    def test_card_name(self):
        self.assertEqual(w.card_name("Speedy  Racer", "speedy"), "Speedy Racer")
        self.assertEqual(w.card_name("𝓢𝓹𝓮𝓮𝓭𝔂 🏎️", "speedy"), "speedy")

    def test_card_draws(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow missing")
        png = w.draw_card("SpeedyRacer_23", 249)
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
