"""Tests for bot/memory.py (no Discord needed)."""
import tempfile
import unittest
from pathlib import Path

from bot import memory


class MemoryTests(unittest.TestCase):
    def test_remembers_across_restarts(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "said.jsonl"
            memory.Memory(path).add("general", "Mod", "Fav track?", "Spa, no contest.", at=0)
            recalled = memory.Memory(path).recall()
            self.assertIn("#general] Mod: Fav track?", recalled)
            self.assertIn("you: Spa, no contest.", recalled)

    def test_keeps_only_the_latest(self):
        with tempfile.TemporaryDirectory() as d:
            m = memory.Memory(Path(d) / "said.jsonl")
            for i in range(memory.KEEP + 5):
                m.add("general", "Mod", f"q{i}", f"a{i}")
            self.assertEqual(len(memory.Memory(m.path).items), memory.KEEP)
            self.assertEqual(m.recall(1).splitlines()[-1], f"  you: a{memory.KEEP + 4}")

    def test_unwritable_path_does_not_crash(self):
        m = memory.Memory("/proc/nope/said.jsonl")
        m.add("general", "Mod", "q", "a")
        self.assertIn("you: a", m.recall())

    def test_timer_line_is_dropped(self):
        self.assertEqual(memory.said("Spa, obviously.\n-# ⏱️ 3 s"), "Spa, obviously.")


if __name__ == "__main__":
    unittest.main()
