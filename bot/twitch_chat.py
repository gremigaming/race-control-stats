"""Race Control in GreMi's Twitch chat: timed messages while the stream is live.

The chat login comes from the "Twitch chat" workflow (login with the account the
messages should come from). That workflow seals the login with a key made from
the Discord bot token, which the workflow and this server both have, and commits
it as bot/twitch_chat.sealed; the bot restarts on that commit and picks it up.
Twitch may hand out a new refresh token, so the newest one is kept on the server
in STATE (never in this public repo).

What to say and how often lives in bot/twitch_timers.json:
  only_live   send only while the channel is live
  gap         minutes between any two timed messages
  timers      [{"text", "every" (minutes), "first" (minutes after going live)}]

Usage from the workflow:
  python3 -m bot.twitch_chat seal <refresh token file>
      (TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET and DISCORD_BOT_TOKEN in the environment)
  python3 -m bot.twitch_chat say "<message>"
      sends one message now (DISCORD_BOT_TOKEN in the environment); if Twitch
      renews the login, the sealed file is rewritten so the bot gets it too
Never logs tokens.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SEALED = HERE / "twitch_chat.sealed"
TIMERS = HERE / "twitch_timers.json"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_TWITCH", "/var/lib/race-control/twitch_chat.json"))
HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
CHECK_EVERY = 60  # seconds

log = logging.getLogger("race_control.twitch")


# ---------------------------------------------------------------- sealing

def _fernet(discord_token):
    from cryptography.fernet import Fernet
    key = hashlib.sha256(b"race-control twitch chat\0" + discord_token.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(login, discord_token):
    return _fernet(discord_token).encrypt(json.dumps(login).encode()).decode()


def unseal(text, discord_token):
    return json.loads(_fernet(discord_token).decrypt(text.strip().encode()))


# ---------------------------------------------------------------- timers

def load_timers(path=TIMERS):
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    data.setdefault("only_live", True)
    data.setdefault("gap", 5)
    for t in data.get("timers", []):
        t.setdefault("first", 0)
    return data


def next_timer(config, live_minutes, sent, last_any, now):
    """Index of the timer to send now, or None.
    live_minutes: minutes since the stream started (None when offline)
    sent: {index: time last sent}, last_any: time of the last timed message."""
    if live_minutes is None and config["only_live"]:
        return None
    if now - last_any < config["gap"] * 60:
        return None
    best = None
    for i, t in enumerate(config.get("timers", [])):
        if live_minutes is not None and live_minutes < t["first"]:
            continue
        last = sent.get(i)
        if last is not None and now - last < t["every"] * 60:
            continue
        # the one waiting longest goes first; never sent counts as waiting longest
        waited = float("inf") if last is None else now - last
        if best is None or waited > best[0]:
            best = (waited, i)
    return None if best is None else best[1]


# ---------------------------------------------------------------- Twitch

class Chat:
    def __init__(self, login, session):
        self.client_id = login["client_id"]
        self.client_secret = login["client_secret"]
        self.refresh_token = login["refresh_token"]
        self.session = session
        self.access = ""
        self.expires = 0
        self.sender = None
        self.broadcaster = None

    async def _refresh(self):
        async with self.session.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "refresh_token", "refresh_token": self.refresh_token,
        }) as r:
            res = await r.json()
            if r.status != 200:
                raise RuntimeError(f"Twitch login refused ({r.status} {res.get('message', '')}), "
                                   "run the Twitch chat workflow with 'login' again")
        self.access = res["access_token"]
        self.expires = time.time() + int(res.get("expires_in", 3600)) - 300
        if res.get("refresh_token") and res["refresh_token"] != self.refresh_token:
            self.refresh_token = res["refresh_token"]
            save_state({"refresh_token": self.refresh_token, "sealed": sealed_id()})

    async def call(self, method, path, **kw):
        for attempt in (0, 1):
            if not self.access or time.time() > self.expires or attempt:
                await self._refresh()
            headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {self.access}"}
            async with self.session.request(method, HELIX + path, headers=headers, **kw) as r:
                if r.status == 401 and not attempt:
                    continue
                res = await r.json(content_type=None) if r.status != 204 else {}
                if r.status >= 300:
                    raise RuntimeError(f"Twitch {path}: {r.status} {res.get('message', '')}")
                return res

    async def setup(self, channel):
        me = (await self.call("GET", "/users"))["data"][0]
        self.sender = me["id"]
        them = (await self.call("GET", "/users", params={"login": channel}))["data"]
        if not them:
            raise RuntimeError(f"Twitch channel {channel} not found")
        self.broadcaster = them[0]["id"]
        log.info("Twitch chat: %s posts in %s", me["login"], channel)

    async def live_since(self):
        data = (await self.call("GET", "/streams",
                                params={"user_id": self.broadcaster}))["data"]
        if not data:
            return None
        from datetime import datetime
        return datetime.fromisoformat(data[0]["started_at"].replace("Z", "+00:00")).timestamp()

    async def say(self, text):
        res = await self.call("POST", "/chat/messages", json={
            "broadcaster_id": self.broadcaster, "sender_id": self.sender, "message": text[:500]})
        sent = (res.get("data") or [{}])[0]
        if not sent.get("is_sent", False):
            reason = (sent.get("drop_reason") or {}).get("message", "unknown reason")
            raise RuntimeError(f"Twitch dropped the message: {reason}")


def sealed_id():
    try:
        return hashlib.sha256(SEALED.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)


def current_login(discord_token):
    """The sealed login, with the newest refresh token kept on the server
    unless a new login was committed since."""
    login = unseal(SEALED.read_text(), discord_token)
    try:
        state = json.loads(STATE.read_text())
        if state.get("sealed") == sealed_id() and state.get("refresh_token"):
            login["refresh_token"] = state["refresh_token"]
    except (OSError, ValueError):
        pass
    return login


async def run(discord_token):
    """Runs next to the Discord bot; never raises."""
    if not SEALED.exists():
        log.info("Twitch chat: no login yet")
        return
    import aiohttp
    try:
        login = current_login(discord_token)
    except Exception as e:
        log.warning("Twitch chat: can't open the login (%s)", type(e).__name__)
        return
    sent, last_any, was_live = {}, 0.0, None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        chat = Chat(login, session)
        while True:
            try:
                config = load_timers()
                if chat.sender is None:
                    await chat.setup(config["channel"])
                since = await chat.live_since()
                if since != was_live:  # stream started or ended: start the timers fresh
                    sent, last_any, was_live = {}, 0.0, since
                now = time.time()
                minutes = None if since is None else (now - since) / 60
                i = next_timer(config, minutes, sent, last_any, now)
                if i is not None:
                    await chat.say(config["timers"][i]["text"])
                    sent[i] = last_any = now
                    log.info("Twitch chat: sent timer %d", i)
            except Exception as e:
                log.warning("Twitch chat: %s", e)
            await asyncio.sleep(CHECK_EVERY)


async def say_once(text, discord_token):
    import aiohttp
    login = unseal(SEALED.read_text(), discord_token)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        chat = Chat(login, session)
        await chat.setup(load_timers()["channel"])
        await chat.say(text)
    if chat.refresh_token != login["refresh_token"]:
        login["refresh_token"] = chat.refresh_token
        SEALED.write_text(seal(login, discord_token) + "\n")


def main(argv):
    if len(argv) == 3 and argv[1] == "say":
        asyncio.run(say_once(argv[2], os.environ["DISCORD_BOT_TOKEN"]))
        print("Sent.")
        return
    if len(argv) == 3 and argv[1] == "seal":
        refresh = pathlib.Path(argv[2]).read_text().strip()
        login = {"client_id": os.environ["TWITCH_CLIENT_ID"],
                 "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
                 "refresh_token": refresh}
        SEALED.write_text(seal(login, os.environ["DISCORD_BOT_TOKEN"]) + "\n")
        print("Sealed the Twitch chat login.")
        return
    sys.exit("usage: python3 -m bot.twitch_chat seal <refresh token file> | say <message>")


if __name__ == "__main__":
    main(sys.argv)
