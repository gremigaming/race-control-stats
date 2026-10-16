"""Tests for bot/wordle.py (no network)."""
import asyncio
import os
import tempfile
import types
import unittest
from unittest import mock

from bot import wordle
import wordle_board


class Wordle(unittest.TestCase):
    def test_setup_starts_from_the_repo_copy_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "sub", "wordle.json")
            with mock.patch.object(wordle, "STATE", wordle.pathlib.Path(state)), \
                    mock.patch.object(wordle_board, "STATE_FILE", wordle_board.STATE_FILE):
                wordle.setup()
                self.assertTrue(os.path.exists(state))
                self.assertEqual(wordle_board.STATE_FILE, state)
                self.assertEqual(wordle_board.WORDLE_CHANNEL_ID, wordle.CHANNEL_ID)
                with open(state, "w") as f:
                    f.write("{}")
                wordle.setup()  # never overwrites the server's results
                with open(state) as f:
                    self.assertEqual(f.read(), "{}")

    def test_only_wordle_posts_wake_the_board(self):
        calls = []

        async def go():
            with mock.patch.object(wordle, "READY", True), mock.patch.object(wordle, "refresh", lambda stats, wait=0: calls.append(wait) or asyncio.sleep(0)):
                wordle.on_message(types.SimpleNamespace(author=types.SimpleNamespace(id=123)), None)
                wordle.on_message(types.SimpleNamespace(
                    author=types.SimpleNamespace(id=int(wordle_board.WORDLE_BOT_ID))), None)
                await asyncio.sleep(0)
        asyncio.run(go())
        self.assertEqual(calls, [wordle.DELAY])


if __name__ == "__main__":
    unittest.main()
