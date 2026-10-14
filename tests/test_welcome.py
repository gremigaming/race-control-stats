import io
import types
import unittest

from PIL import Image

from bot import welcome


def member(*role_ids, bot=False):
    return types.SimpleNamespace(roles=[types.SimpleNamespace(id=r) for r in role_ids], bot=bot)


class WelcomeTest(unittest.TestCase):
    def test_card_is_a_png(self):
        av = io.BytesIO()
        Image.new("RGB", (64, 64), (1, 2, 3)).save(av, "PNG")
        png = welcome.card("A very long racing name that needs shrinking", av.getvalue(), 250)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (welcome.W, welcome.H))

    def test_card_survives_a_broken_avatar(self):
        self.assertTrue(welcome.card("Max", b"not an image", 1).startswith(b"\x89PNG"))

    def test_only_new_members_are_greeted(self):
        m = welcome.MEMBER_ROLE_ID
        self.assertTrue(welcome.became_member(member(), member(m)))
        self.assertFalse(welcome.became_member(member(m), member(m)))
        self.assertFalse(welcome.became_member(member(), member(m, bot=True)))

    def test_printable_drops_emoji(self):
        self.assertEqual(welcome.printable("🏎️ Max ⚡", "max"), "Max")
        self.assertEqual(welcome.printable("🔥", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
