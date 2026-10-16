"""Keeps the Wordle board in the WORDLE channel fresh (wordle_board.py).

GitHub runs the 5-minute stats schedule only every few hours, so the board
waited until noon for the morning results (2026-10-16). Race Control is always
online: a Wordle app post wakes it, and it redraws the board a little later
(the app adds its picture after the text). A check every 30 minutes catches
anything missed while the bot was restarting.

The results live on this server (STATE), started once from wordle.json in the
repo, so the bot never changes files in its own git checkout.
"""
import asyncio
import json
import logging
import os
import pathlib
import shutil
import time
import types
import urllib.error
import urllib.request

import wordle_board

STATE = pathlib.Path(os.environ.get("RACE_CONTROL_WORDLE", "/var/lib/race-control/wordle.json"))
SEED = pathlib.Path(__file__).resolve().parent.parent / "wordle.json"
CHANNEL_ID = "1560191888961708042"  # 🟩┃WORDLE
ROLE_ID = "1560191841264214096"     # Wordle Player, the role that shows the channel
SOURCES = ["1079917340298321942", CHANNEL_ID]  # GENERAL, where the app posts, and WORDLE
API = "https://discord.com/api/v10"
UA = "DiscordBot (https://github.com/race-control-stats, 1.0)"
DELAY = 20       # seconds after a Wordle post before redrawing
EVERY = 30 * 60  # seconds between checks without a post
log = logging.getLogger("race_control.wordle")
_lock = asyncio.Lock()
READY = False  # set by setup(); without it nothing runs, so the repo copy is never written


def _stats(token, guild_id):
    """What wordle_board needs from update_stats.py: Discord calls with the bot token."""
    def discord(method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        headers = {"Authorization": f"Bot {token}", "User-Agent": UA}
        if body is not None:
            headers["Content-Type"] = "application/json"
        for _ in range(6):
            req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.2)
                    continue
                if e.code >= 500:
                    time.sleep(2)
                    continue
                raise RuntimeError(f"{method} {path.split('?')[0]} failed: {e.code} {e.read()[:200]!r}")
            except urllib.error.URLError:
                time.sleep(2)
        raise RuntimeError(f"{method} {path.split('?')[0]} failed after retries")
    return types.SimpleNamespace(discord=discord, GUILD_ID=str(guild_id), DISCORD=API, UA=UA,
                                 DISCORD_TOKEN=token)


def setup():
    """Points wordle_board at this server's copy of the results."""
    if not STATE.exists():
        STATE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SEED, STATE)
    wordle_board.STATE_FILE = str(STATE)
    wordle_board.WORDLE_CHANNEL_ID = CHANNEL_ID
    wordle_board.WORDLE_ROLE_ID = ROLE_ID
    wordle_board.SOURCE_CHANNELS = SOURCES
    global READY
    READY = True


async def refresh(stats, wait=0):
    await asyncio.sleep(wait)
    async with _lock:
        try:
            await asyncio.to_thread(wordle_board.update, stats)
        except Exception as e:
            log.warning("wordle board not updated: %s", e)


def on_message(message, stats):
    """Call for every message: a Wordle app post redraws the board soon after."""
    if READY and str(message.author.id) == wordle_board.WORDLE_BOT_ID:
        asyncio.create_task(refresh(stats, DELAY))


async def run(stats):
    while READY:
        await refresh(stats)
        await asyncio.sleep(EVERY)
