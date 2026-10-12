"""Race Control in GreMi's YouTube live chat, next to Twitch (see twitch_chat.py).

While GreMi is live on YouTube the bot reads the live chat, counts it toward
chat activity, and posts the same timed messages as on Twitch. GreMi or a
YouTube mod can type a timer's command (like !simgasm): the message goes out
on YouTube and Twitch both, so the stream popups fire too.

Login: the "YouTube" workflow (input `login`) shows a code to enter at
google.com/device while signed in to the YouTube channel. The login is sealed
like the Twitch ones (bot/youtube.sealed) and the bot picks it up by itself.
Needs YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET (a Google OAuth client of type
"TVs and Limited Input devices", YouTube Data API v3 enabled).

Quota: YouTube allows 10,000 units a day. Reading chat costs 5 per call (every
POLL_MIN seconds or slower while live), the live check 1 per call, a message 50.

Usage:
  python3 -m bot.youtube_chat login-start            prints the code (workflow step 1)
  python3 -m bot.youtube_chat login-finish CODE N S  waits for approval and seals it
  python3 -m bot.youtube_chat say "message"          sends a message in the live chat now
"""
import asyncio
import collections
import json
import logging
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

from bot import twitch_chat

HERE = pathlib.Path(__file__).resolve().parent
SEALED = HERE / "youtube.sealed"
API = "https://www.googleapis.com/youtube/v3"
DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube"
LIVE_EVERY = 120   # seconds between "is GreMi live on YouTube?" checks
POLL_MIN = 8       # seconds between chat reads at the fastest

log = logging.getLogger("race_control.youtube")


class YouTube:
    def __init__(self, login, session, keep=True):
        self.login = login
        self.session = session
        self.keep = keep  # write renewed logins to the server state (on the server only)
        self.access, self.expires = "", 0
        self.chat_id = None
        self.sent = collections.deque(maxlen=20)

    async def token(self):
        if time.time() < self.expires - 60:
            return self.access
        async with self.session.post(TOKEN_URL, data={
                "client_id": self.login["client_id"], "client_secret": self.login["client_secret"],
                "refresh_token": self.login["refresh_token"], "grant_type": "refresh_token"}) as r:
            res = await r.json(content_type=None)
            if r.status != 200:
                raise RuntimeError(f"YouTube login refused ({r.status} {res.get('error', '')})")
        self.access, self.expires = res["access_token"], time.time() + int(res.get("expires_in", 3600))
        return self.access

    async def call(self, method, path, params=None, body=None):
        headers = {"Authorization": f"Bearer {await self.token()}"}
        async with self.session.request(method, API + path, params=params, json=body,
                                        headers=headers) as r:
            res = await r.json(content_type=None) if r.status != 204 else {}
            if r.status >= 300:
                err = (res.get("error") or {}).get("message", "")
                raise RuntimeError(f"YouTube {path}: {r.status} {err}")
            return res

    async def live_chat(self):
        """The live chat id of the broadcast that is live now, else None."""
        res = await self.call("GET", "/liveBroadcasts", params={
            "part": "snippet", "broadcastStatus": "active", "broadcastType": "all"})
        for item in res.get("items", []):
            chat = item["snippet"].get("liveChatId")
            if chat:
                return chat
        return None

    async def say(self, text):
        if not self.chat_id:
            return
        self.sent.append(text[:200].strip())
        await self.call("POST", "/liveChat/messages", params={"part": "snippet"}, body={
            "snippet": {"liveChatId": self.chat_id, "type": "textMessageEvent",
                        "textMessageDetails": {"messageText": text[:200]}}})


async def run(discord_token):
    """Runs next to the Discord bot; never raises."""
    if not SEALED.exists():
        return
    import aiohttp
    try:
        login = twitch_chat.unseal(SEALED.read_text(), discord_token)
    except Exception as e:
        await twitch_chat.report(f"YouTube: can't open the login ({type(e).__name__})")
        return
    answered = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        yt = YouTube(login, session)
        twitch_chat.MIRRORS.append(yt.say)
        await twitch_chat.report("YouTube login loaded")
        page, checked = None, 0.0
        while True:
            wait = LIVE_EVERY
            try:
                now = time.time()
                if not yt.chat_id or now - checked >= LIVE_EVERY:
                    chat, checked = await yt.live_chat(), now
                    if chat != yt.chat_id:
                        yt.chat_id, page = chat, None
                        if chat:
                            await twitch_chat.report("YouTube: live, reading chat")
                if yt.chat_id:
                    res = await yt.call("GET", "/liveChat/messages", params=dict(
                        {"liveChatId": yt.chat_id, "part": "snippet,authorDetails",
                         "maxResults": 200}, **({"pageToken": page} if page else {})))
                    first = page is None  # the first page is old messages: skip them
                    page = res.get("nextPageToken")
                    wait = max(POLL_MIN, int(res.get("pollingIntervalMillis", 0)) / 1000)
                    if not first:
                        await handle(yt, res.get("items", []), answered)
                    if res.get("offlineAt"):
                        yt.chat_id = None
            except Exception as e:
                await twitch_chat.report(f"YouTube problem: {e}")
                if "liveChat" in str(e):
                    yt.chat_id = None
            await asyncio.sleep(wait)


async def handle(yt, items, answered):
    config = twitch_chat.load_timers()
    for item in items:
        author = item.get("authorDetails") or {}
        text = (item.get("snippet") or {}).get("displayMessage", "").strip()
        if text in yt.sent:  # our own message coming back (we post as the channel)
            continue
        if twitch_chat.ROOM is not None:
            twitch_chat.ROOM.chat.append(time.time())
        badges = ({"broadcaster"} if author.get("isChatOwner") else set()) | \
                 ({"moderator"} if author.get("isChatModerator") else set())
        i = twitch_chat.chat_command(config, text, badges)
        if i is None or time.time() - answered.get(i, 0) <= twitch_chat.COMMAND_GAP:
            continue
        answered[i] = time.time()
        if twitch_chat.ROOM is not None:
            twitch_chat.ROOM.commands[i] = time.time()
        message = config["timers"][i]["text"]
        await yt.say(message)
        if twitch_chat.SAY is not None:  # Twitch too, so the stream popups fire
            await twitch_chat.SAY(message)
        log.info("YouTube chat: %s", config["timers"][i].get("command"))


# ---------------------------------------------------------------- login and one-offs

def _post(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def login_start():
    status, res = _post(DEVICE_URL, {"client_id": os.environ["YOUTUBE_CLIENT_ID"], "scope": SCOPE})
    if status != 200:
        sys.exit(f"Google refused: {status} {res}")
    print("=" * 60)
    print(f"Go to {res['verification_url']} and enter the code:  {res['user_code']}")
    print("Sign in with the Google account that owns the YouTube channel.")
    print("=" * 60)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"device_code={res['device_code']}\ninterval={res.get('interval', 5)}\n"
                    f"expires_in={res.get('expires_in', 1800)}\n")


def login_finish(device_code, interval, expires_in, discord_token):
    deadline = time.time() + int(expires_in)
    while time.time() < deadline:
        time.sleep(int(interval))
        status, res = _post(TOKEN_URL, {
            "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"], "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if status == 200:
            login = {"client_id": os.environ["YOUTUBE_CLIENT_ID"],
                     "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
                     "refresh_token": res["refresh_token"]}
            SEALED.write_text(twitch_chat.seal(login, discord_token) + "\n")
            print("Approved; sealed the YouTube login.")
            return
        if res.get("error") == "slow_down":
            interval = int(interval) + 5
        elif res.get("error") != "authorization_pending":
            sys.exit(f"Login failed: {res}")
    sys.exit("The code ran out before it was approved; run the login again.")


async def say_once(text, discord_token):
    import aiohttp
    login = twitch_chat.unseal(SEALED.read_text(), discord_token)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        yt = YouTube(login, session, keep=False)
        yt.chat_id = await yt.live_chat()
        if not yt.chat_id:
            sys.exit("Not live on YouTube right now, so there is no chat to post in.")
        await yt.say(text)


def main(argv):
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if len(argv) == 2 and argv[1] == "login-start":
        login_start()
    elif len(argv) == 5 and argv[1] == "login-finish":
        login_finish(argv[2], argv[3], argv[4], token)
    elif len(argv) == 3 and argv[1] == "say":
        asyncio.run(say_once(argv[2], token))
        print("Sent.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
