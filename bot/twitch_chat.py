"""Race Control in GreMi's Twitch chat: timed messages and ad breaks while live.

Two Twitch logins, both from the "Twitch chat" workflow:
  login        the bot account (gremi_bot) that reads and writes chat
               -> bot/twitch_chat.sealed
  login-owner  GreMi's own account, only for ads (Twitch lets only the channel
               owner run them) -> bot/twitch_owner.sealed
The workflow seals each login with a key made from the Discord bot token, which
the workflow and this server both have, and commits it; the bot restarts on that
commit and picks it up. Twitch may hand out a new refresh token, so the newest
one is kept on the server in STATE (never in this public repo).

The bot listens to chat (EventSub over a websocket) so it knows how busy chat is:
  - timed messages go out only when people are chatting (min_chat messages
    since the last one), so they don't talk into an empty room
  - ads go where they hurt least: right after a race ends (a new race file from
    the uploader on the racing PC lands in race-stats) or when GreMi or a mod
    types !ad in chat. When Twitch's own scheduled ad is about to hit and no
    race just ended (so GreMi is probably driving), it is snoozed.

Everything is set in bot/twitch_timers.json:
  only_live   send only while the channel is live
  gap         minutes between any two timed messages
  min_chat    chat messages from viewers needed since the last timed message
  timers      [{"text", "every" (minutes), "first" (minutes after going live)}]
  ads         {"auto": run an ad after each race, "length": seconds (30-180),
               "min_gap": minutes between our ads, "notice": chat text first
               ({length} is filled in), "snooze_while_driving": true/false,
               "command": true to allow !ad from GreMi and mods}

Usage from the workflow (DISCORD_BOT_TOKEN in the environment):
  python3 -m bot.twitch_chat seal <chat|owner> <refresh token file>
      (also TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET)
  python3 -m bot.twitch_chat say "<message>"   one chat message now
  python3 -m bot.twitch_chat ad [seconds]       one ad now (only while live)
  python3 -m bot.twitch_chat ads                when Twitch's next ad is due
If Twitch renews a login there, the sealed file is rewritten so the bot gets it too.
Never logs tokens.
"""
import asyncio
import base64
import collections
import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SEALED = {"chat": HERE / "twitch_chat.sealed", "owner": HERE / "twitch_owner.sealed"}
TIMERS = HERE / "twitch_timers.json"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_TWITCH", "/var/lib/race-control/twitch_chat.json"))
HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
EVENTSUB = "wss://eventsub.wss.twitch.tv/ws"
RACES_URL = "https://api.github.com/repos/gremigaming/race-stats/commits?path=races&per_page=1"
TICK = 10          # seconds between checks
LIVE_EVERY = 60    # seconds between live checks
RACES_EVERY = 20   # seconds between race file checks (ETag, so 304s are free)
ADS_EVERY = 60     # seconds between ad schedule checks
RACE_FRESH = 180   # a race that ended this many seconds ago still counts as "between races"

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


# ---------------------------------------------------------------- decisions

def load_timers(path=TIMERS):
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    data.setdefault("only_live", True)
    data.setdefault("gap", 5)
    data.setdefault("min_chat", 0)
    for t in data.get("timers", []):
        t.setdefault("first", 0)
    ads = data.setdefault("ads", {})
    ads.setdefault("auto", False)
    ads.setdefault("length", 90)
    ads.setdefault("min_gap", 20)
    ads.setdefault("notice", "")
    ads.setdefault("snooze_while_driving", False)
    ads.setdefault("command", True)
    ads["length"] = min(180, max(30, int(ads["length"])))
    return data


def next_timer(config, live_minutes, sent, last_any, now, chatted=None):
    """Index of the timer to send now, or None.
    live_minutes: minutes since the stream started (None when offline)
    sent: {index: time last sent}, last_any: time of the last timed message
    chatted: viewer chat messages since the last timed message (None = don't check)."""
    if live_minutes is None and config["only_live"]:
        return None
    if now - last_any < config["gap"] * 60:
        return None
    if chatted is not None and chatted < config["min_chat"]:
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


def ad_now(ads, live, now, last_ad, race_ended, asked):
    """Why to run an ad now ('asked' or 'race'), or None.
    race_ended: when the last race ended (0 if none this stream)
    asked: GreMi or a mod typed !ad."""
    if not live:
        return None
    if asked:
        return "asked"
    if ads["auto"] and race_ended > last_ad and now - race_ended < RACE_FRESH \
            and now - last_ad >= ads["min_gap"] * 60:
        return "race"
    return None


def snooze_now(ads, schedule, now, race_ended):
    """True when Twitch's own ad is about to hit while GreMi is probably driving."""
    if not ads["snooze_while_driving"] or not schedule:
        return False
    due = schedule.get("next_ad_at") or 0
    due = int(due) if str(due).isdigit() else 0
    if not due or due - now > 120 or due < now:
        return False
    if now - race_ended < RACE_FRESH:  # between races: let it run
        return False
    return int(schedule.get("snooze_count") or 0) > 0


# ---------------------------------------------------------------- Twitch

def sealed_id(kind):
    try:
        return hashlib.sha256(SEALED[kind].read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def read_state():
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}
    if "refresh_token" in state:  # the first version kept only the chat login
        state = {"chat": state}
    return state


def save_refresh(kind, token):
    state = read_state()
    state[kind] = {"refresh_token": token, "sealed": sealed_id(kind)}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)


def current_login(kind, discord_token):
    """The sealed login, with the newest refresh token kept on the server
    unless a new login was committed since."""
    login = unseal(SEALED[kind].read_text(), discord_token)
    mine = read_state().get(kind) or {}
    if mine.get("sealed") == sealed_id(kind) and mine.get("refresh_token"):
        login["refresh_token"] = mine["refresh_token"]
    return login


class Twitch:
    def __init__(self, kind, login, session, keep=True):
        self.kind = kind
        self.client_id = login["client_id"]
        self.client_secret = login["client_secret"]
        self.refresh_token = login["refresh_token"]
        self.session = session
        self.keep = keep  # write renewed logins to STATE (on the server only)
        self.access = ""
        self.expires = 0
        self.me = None
        self.broadcaster = None

    async def _refresh(self):
        async with self.session.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "refresh_token", "refresh_token": self.refresh_token,
        }) as r:
            res = await r.json()
            if r.status != 200:
                raise RuntimeError(f"Twitch {self.kind} login refused ({r.status} "
                                   f"{res.get('message', '')}), run the Twitch chat workflow "
                                   f"with '{'login' if self.kind == 'chat' else 'login-owner'}' again")
        self.access = res["access_token"]
        self.expires = time.time() + int(res.get("expires_in", 3600)) - 300
        if res.get("refresh_token") and res["refresh_token"] != self.refresh_token:
            self.refresh_token = res["refresh_token"]
            if self.keep:
                save_refresh(self.kind, self.refresh_token)

    async def token(self):
        if not self.access or time.time() > self.expires:
            await self._refresh()
        return self.access

    async def call(self, method, path, **kw):
        for attempt in (0, 1):
            if attempt:
                await self._refresh()
            headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {await self.token()}"}
            async with self.session.request(method, HELIX + path, headers=headers, **kw) as r:
                if r.status == 401 and not attempt:
                    continue
                res = await r.json(content_type=None) if r.status != 204 else {}
                if r.status >= 300:
                    raise RuntimeError(f"Twitch {path}: {r.status} {res.get('message', '')}")
                return res

    async def setup(self, channel):
        self.me = (await self.call("GET", "/users"))["data"][0]
        them = (await self.call("GET", "/users", params={"login": channel}))["data"]
        if not them:
            raise RuntimeError(f"Twitch channel {channel} not found")
        self.broadcaster = them[0]["id"]
        log.info("Twitch %s: %s in %s", self.kind, self.me["login"], channel)

    async def live_since(self):
        data = (await self.call("GET", "/streams", params={"user_id": self.broadcaster}))["data"]
        if not data:
            return None
        return dt.datetime.fromisoformat(data[0]["started_at"].replace("Z", "+00:00")).timestamp()

    async def say(self, text):
        res = await self.call("POST", "/chat/messages", json={
            "broadcaster_id": self.broadcaster, "sender_id": self.me["id"], "message": text[:500]})
        sent = (res.get("data") or [{}])[0]
        if not sent.get("is_sent", False):
            reason = (sent.get("drop_reason") or {}).get("message", "unknown reason")
            raise RuntimeError(f"Twitch dropped the message: {reason}")

    # owner only
    async def run_ad(self, length):
        res = await self.call("POST", "/channels/commercial",
                              json={"broadcaster_id": self.me["id"], "length": length})
        return (res.get("data") or [{}])[0]

    async def ad_schedule(self):
        res = await self.call("GET", "/channels/ads", params={"broadcaster_id": self.me["id"]})
        return (res.get("data") or [{}])[0]

    async def snooze(self):
        res = await self.call("POST", "/channels/ads/schedule/snooze",
                              params={"broadcaster_id": self.me["id"]})
        return (res.get("data") or [{}])[0]


# ---------------------------------------------------------------- running on the server

class Room:
    """What the listeners saw: chat messages and finished races."""
    def __init__(self):
        self.chat = collections.deque(maxlen=2000)  # times of viewer messages
        self.asked = False                           # !ad from GreMi or a mod
        self.race_ended = 0.0

    def since(self, t):
        return sum(1 for x in self.chat if x > t)


async def listen_chat(bot, room, ads_command):
    """Counts viewer messages (EventSub websocket); reconnects by itself."""
    url = EVENTSUB
    while True:
        try:
            async with bot.session.ws_connect(url, heartbeat=None) as ws:
                url = EVENTSUB
                async for msg in ws:
                    data = json.loads(msg.data)
                    kind = data["metadata"]["message_type"]
                    if kind == "session_welcome":
                        await bot.call("POST", "/eventsub/subscriptions", json={
                            "type": "channel.chat.message", "version": "1",
                            "condition": {"broadcaster_user_id": bot.broadcaster,
                                          "user_id": bot.me["id"]},
                            "transport": {"method": "websocket",
                                          "session_id": data["payload"]["session"]["id"]}})
                        log.info("Twitch chat: listening")
                    elif kind == "session_reconnect":
                        url = data["payload"]["session"]["reconnect_url"]
                        break
                    elif kind == "notification":
                        ev = data["payload"]["event"]
                        if ev.get("chatter_user_id") == bot.me["id"]:
                            continue
                        badges = {b.get("set_id") for b in ev.get("badges") or []}
                        text = (ev.get("message") or {}).get("text", "").strip().lower()
                        if text.split()[:1] == ["!ad"]:
                            if ads_command and badges & {"broadcaster", "moderator"}:
                                room.asked = True
                            continue
                        room.chat.append(time.time())
        except Exception as e:
            log.warning("Twitch chat listener: %s", e)
        await asyncio.sleep(5 if url == EVENTSUB else 0)


async def watch_races(session, room, live):
    """Notices a new race file in race-stats (the uploader sends it right after a race)."""
    etag, last = None, None
    while True:
        try:
            if live():
                headers = {"If-None-Match": etag} if etag else {}
                async with session.get(RACES_URL, headers=headers) as r:
                    if r.status == 200:
                        etag = r.headers.get("ETag")
                        commits = await r.json()
                        sha = commits[0]["sha"] if commits else None
                        message = commits[0]["commit"]["message"] if commits else ""
                        if last is not None and sha != last and message.startswith("race:"):
                            room.race_ended = time.time()
                            log.info("Twitch: a race just ended")
                        last = sha
        except Exception as e:
            log.warning("Twitch race watch: %s", e)
        await asyncio.sleep(RACES_EVERY)


async def run(discord_token):
    """Runs next to the Discord bot; never raises."""
    if not SEALED["chat"].exists():
        log.info("Twitch chat: no login yet")
        return
    import aiohttp
    logins = {}
    for kind in SEALED:
        if SEALED[kind].exists():
            try:
                logins[kind] = current_login(kind, discord_token)
            except Exception as e:
                log.warning("Twitch %s: can't open the login (%s)", kind, type(e).__name__)
    if "chat" not in logins:
        return
    room = Room()
    sent, last_any, was_live, since = {}, 0.0, None, None
    last_ad, schedule, checked_live, checked_ads = 0.0, None, 0.0, 0.0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        bot = Twitch("chat", logins["chat"], session)
        owner = Twitch("owner", logins["owner"], session) if "owner" in logins else None
        tasks = []
        while True:
            try:
                config = load_timers()
                ads = config["ads"]
                if bot.me is None:
                    await bot.setup(config["channel"])
                if not tasks:
                    tasks = [asyncio.create_task(listen_chat(bot, room, ads["command"])),
                             asyncio.create_task(watch_races(session, room,
                                                             lambda: was_live is not None))]
                if owner and owner.me is None:
                    await owner.setup(config["channel"])
                now = time.time()
                if now - checked_live >= LIVE_EVERY:
                    since, checked_live = await bot.live_since(), now
                if since != was_live:  # stream started or ended: start fresh
                    sent, last_any, was_live = {}, 0.0, since
                    last_ad, room.race_ended, room.asked = 0.0, 0.0, False
                minutes = None if since is None else (now - since) / 60

                i = next_timer(config, minutes, sent, last_any, now,
                               room.since(last_any or (since or now)))
                if i is not None:
                    await bot.say(config["timers"][i]["text"])
                    sent[i] = last_any = now
                    log.info("Twitch chat: sent timer %d", i)

                if owner:
                    why = ad_now(ads, since is not None, now, last_ad, room.race_ended, room.asked)
                    room.asked = False
                    if why:
                        last_ad = now
                        if ads["notice"]:
                            await bot.say(ads["notice"].format(length=ads["length"]))
                        res = await owner.run_ad(ads["length"])
                        log.info("Twitch: ad (%s) %s s", why, res.get("length"))
                    if since is not None and now - checked_ads >= ADS_EVERY:
                        schedule, checked_ads = await owner.ad_schedule(), now
                        if snooze_now(ads, schedule, now, room.race_ended):
                            schedule = await owner.snooze()
                            log.info("Twitch: snoozed the scheduled ad (driving)")
            except Exception as e:
                log.warning("Twitch: %s", e)
            await asyncio.sleep(TICK)


# ---------------------------------------------------------------- one-off commands

async def once(kind, discord_token, action):
    import aiohttp
    login = unseal(SEALED[kind].read_text(), discord_token)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        client = Twitch(kind, login, session, keep=False)
        await client.setup(load_timers()["channel"])
        out = await action(client)
    if client.refresh_token != login["refresh_token"]:
        login["refresh_token"] = client.refresh_token
        SEALED[kind].write_text(seal(login, discord_token) + "\n")
    return out


def when(value):
    if not value or not str(value).isdigit() or int(value) == 0:
        return "none"
    t = dt.datetime.fromtimestamp(int(value), dt.timezone.utc)
    from zoneinfo import ZoneInfo
    return f"{t.astimezone(ZoneInfo('Europe/Amsterdam')):%a %d %b %H:%M:%S}"


def main(argv):
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if len(argv) == 4 and argv[1] == "seal" and argv[2] in SEALED:
        refresh = pathlib.Path(argv[3]).read_text().strip()
        login = {"client_id": os.environ["TWITCH_CLIENT_ID"],
                 "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
                 "refresh_token": refresh}
        SEALED[argv[2]].write_text(seal(login, token) + "\n")
        print(f"Sealed the Twitch {argv[2]} login.")
    elif len(argv) == 3 and argv[1] == "say":
        asyncio.run(once("chat", token, lambda c: c.say(argv[2])))
        print("Sent.")
    elif len(argv) in (2, 3) and argv[1] == "ad":
        length = min(180, max(30, int(argv[2]) if len(argv) == 3 else load_timers()["ads"]["length"]))
        res = asyncio.run(once("owner", token, lambda c: c.run_ad(length)))
        print(f"Ad running: {res.get('length')} seconds. {res.get('message', '')}".strip())
    elif len(argv) == 2 and argv[1] == "ads":
        s = asyncio.run(once("owner", token, lambda c: c.ad_schedule()))
        print(f"Next scheduled ad: {when(s.get('next_ad_at'))} ({s.get('duration')} s), "
              f"last ad: {when(s.get('last_ad_at'))}, snoozes left: {s.get('snooze_count')}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
