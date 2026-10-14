"""Race Control in GreMi's Twitch chat: timed messages and ad breaks while live.

Two Twitch logins, both from the "Twitch chat" workflow:
  login        the bot account (gremi_bot) that reads and writes chat
               -> bot/twitch_chat.sealed
  login-owner  GreMi's own account, for ads, new subs (giveaway), VIPs, the
               stream title and channel points (Twitch lets only the channel
               owner run them)
               -> bot/twitch_owner.sealed
The workflow seals each login with a key made from the Discord bot token, which
the workflow and this server both have, and commits it; the bot restarts on that
commit and picks it up. Twitch may hand out a new refresh token, so the newest
one is kept on the server in STATE (never in this public repo).

The bot listens to chat (EventSub over a websocket) so it knows how busy chat is:
  - timed messages go out only when people are chatting (min_chat messages
    since the last one), so they don't talk into an empty room
  - ads: GreMi wants 3 minutes of ads per stream hour. Short ads run during a
    race when chat is quiet (fewest people watching then); if the hour is
    still short later on, one longer ad tops it up in the lobby right after a
    race (a race has ended when the uploader on the racing PC sends a new race
    file to race-stats). Twitch's own scheduled ads count toward the hour, and
    one about to hit while chat is busy is snoozed. GreMi or a mod can type !ad, or a timer's
    "command" (like !simgasm) to send that message right away.

Everything is set in bot/twitch_timers.json:
  only_live   send only while the channel is live
  gap         minutes between any two timed messages
  min_chat    chat messages from viewers needed since the last timed message
  timers      [{"text", "every" (minutes), "first" (minutes after going live)}]
  ads         auto (on/off), target_per_hour (seconds), race_length (seconds),
              quiet_minutes + quiet_max (chat counts as quiet with at most
              quiet_max messages in quiet_minutes), min_gap (minutes between
              race ads), first (minutes after going live), lobby_after (minutes
              into the hour before a lobby top-up), notice_race / notice_lobby
              (chat text before the ad, {length} filled in, "" for none),
              snooze_when_busy, command (allow !ad), length (seconds for !ad)

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
TITLES = HERE / "stream_titles.json"
TITLES_URL = "https://raw.githubusercontent.com/gremigaming/race-control-stats/main/bot/stream_titles.json"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_TWITCH", "/var/lib/race-control/twitch_chat.json"))
HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
EVENTSUB = "wss://eventsub.wss.twitch.tv/ws"
RACES_URL = "https://api.github.com/repos/gremigaming/race-stats/commits?path=races&per_page=1"
TICK = 10          # seconds between checks
COMMAND_GAP = 60   # seconds before the same !command or question is answered again
EVENT_GAP = 60     # seconds between an event message and any other Race Control message
REPLY_DELAY = 3    # seconds before answering a question or !command, so it doesn't feel instant
RACE_API = "https://api.github.com/repos/gremigaming/race-stats/commits/{}"
RACE_RAW = "https://raw.githubusercontent.com/gremigaming/race-stats/main/{}"
LIVE_EVERY = 60    # seconds between live checks
RACES_EVERY = 20   # seconds between race file checks (ETag, so 304s are free)
ADS_EVERY = 60     # seconds between ad schedule checks
TITLE_EVERY = 300  # seconds between stream title checks
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
        t.setdefault("events", {})
        cmds = t.get("commands") or ([t["command"]] if t.get("command") else [])
        t["commands"] = [c.lower() for c in cmds]
    ads = data.setdefault("ads", {})
    for key, value in {"auto": False, "length": 90, "race_length": 30, "target_per_hour": 180,
                       "first": 10, "quiet_minutes": 3, "quiet_max": 1, "min_gap": 10,
                       "lobby_after": 30, "notice_lobby": "", "notice_race": "",
                       "snooze_when_busy": False, "command": True}.items():
        ads.setdefault(key, value)
    for key in ("length", "race_length"):
        ads[key] = min(180, max(30, int(ads[key])))
    return data


def pick_title(titles, used):
    """The first title not used yet; when all were used, the one used longest ago."""
    titles = [t.strip()[:140] for t in titles if t and t.strip()]
    if not titles:
        return None
    fresh = [t for t in titles if t not in used]
    if fresh:
        return fresh[0]
    return min(titles, key=used.index)


def segment_to_title(segments, now, before_minutes, titled):
    """The next scheduled stream that starts within before_minutes and has no title from us yet."""
    for seg in segments or []:
        if seg.get("canceled_until"):
            continue
        start = stamp(seg.get("start_time"))
        if start and now <= start + 15 * 60 and start - now <= before_minutes * 60:
            if seg.get("id") not in titled:
                return seg
            return None
    return None


def game_ok(timer, game):
    return not timer.get("game") or timer["game"].lower() in (game or "").lower()


def timer_index(config, tid):
    for i, t in enumerate(config.get("timers", [])):
        if t.get("id") == tid:
            return i
    return None


def queue_event(config, events, kind, now, game=None):
    """Adds (due time, timer index) for every timer that reacts to this event
    ("events": {"new_chatter": 2, ...} = minutes to wait)."""
    for i, t in enumerate(config.get("timers", [])):
        if kind in t.get("events", {}) and game_ok(t, game):
            events.append((now + float(t["events"][kind]) * 60, i))


def due_event(config, events, sent, last_any, now, game=None):
    """Index of an event message to send now, or None. Events whose timer is on
    its "cooldown" (minutes) are dropped; they wait for EVENT_GAP after any message."""
    timers = config.get("timers", [])
    for item in sorted(events):
        due, i = item
        if due > now:
            break
        t = timers[i] if i < len(timers) else None
        if t is None or not game_ok(t, game) or now - sent.get(i, -1e12) < t.get("cooldown", 0) * 60:
            events.remove(item)
            continue
        if now - last_any < EVENT_GAP:
            return None
        events.remove(item)
        return i
    return None


def hidden_keys(data):
    """One key per human driver with their name hidden in a raw race file: race
    number + nationality + platform, or the car name for the shared default (#2)."""
    keys = set()
    for d in data.get("classification-data") or []:
        p = d.get("participant-data") or {}
        if p.get("ai-controlled") or p.get("show-online-names") is not False:
            continue
        number, nat = p.get("race-number"), p.get("nationality") or "Unspecified"
        if number == 2 and nat == "Unspecified":
            keys.add("car:" + (d.get("driver-name") or ""))
        else:
            keys.add(f"{number}|{nat}|{p.get('platform')}")
    return keys


def next_timer(config, live_minutes, sent, last_any, now, chatted=None, game=None):
    """Index of the timer to send now, or None.
    live_minutes: minutes since the stream started (None when offline)
    sent: {index: time last sent}, last_any: time of the last timed message
    chatted: viewer chat messages since the last timed message (None = don't check)
    game: the stream's Twitch category; a timer with "game" only goes out when it's in there.
    Only timers with "every" run by the clock; "quiet" = minutes without any Race
    Control message first; "skip_after" = {"id", "minutes"}: not when that timer went
    out that recently."""
    if live_minutes is None and config["only_live"]:
        return None
    if now - last_any < config["gap"] * 60:
        return None
    if chatted is not None and chatted < config["min_chat"]:
        return None
    best = None
    timers = config.get("timers", [])
    for i, t in enumerate(timers):
        if not t.get("every"):
            continue
        if live_minutes is not None and live_minutes < t["first"]:
            continue
        if not game_ok(t, game):
            continue
        if now - last_any < t.get("quiet", 0) * 60:
            continue
        skip = t.get("skip_after")
        if skip:
            j = timer_index(config, skip.get("id"))
            if j is not None and now - sent.get(j, -1e12) < skip.get("minutes", 0) * 60:
                continue
        last = sent.get(i)
        if last is not None and now - last < t["every"] * 60:
            continue
        # the one waiting longest goes first; never sent counts as waiting longest
        waited = float("inf") if last is None else now - last
        if best is None or waited > best[0]:
            best = (waited, i)
    return None if best is None else best[1]


def plan_ad(ads, now, live_minutes, last_race_ad, race_ended, lobby_done, asked,
            recent_chat, hour_seconds, hour_minutes):
    """(why, seconds) for an ad to run now, or None.
    live_minutes: minutes since the stream started (None when offline)
    last_race_ad: when our last ad during a race ran
    race_ended: when the last race ended (0 if none yet this stream)
    lobby_done: race_ended of the last race we already checked for a lobby ad
    asked: GreMi or a mod typed !ad
    recent_chat: viewer messages in the last quiet_minutes
    hour_seconds / hour_minutes: ad seconds so far and minutes into this stream hour."""
    if live_minutes is None:
        return None
    if asked:
        return "asked", ads["length"]
    if not ads["auto"] or live_minutes < ads["first"]:
        return None
    missing = ads["target_per_hour"] - hour_seconds
    if missing <= 0:
        return None
    in_lobby = race_ended and now - race_ended < RACE_FRESH
    if in_lobby:
        if race_ended != lobby_done and hour_minutes >= ads["lobby_after"]:
            return "lobby", min(180, -(-missing // 30) * 30)  # rounded up to 30 s steps
        return None
    if recent_chat <= ads["quiet_max"] and now - last_race_ad >= ads["min_gap"] * 60:
        return "race", ads["race_length"]
    return None


def stamp(value):
    """Twitch ad times (RFC3339, or Unix seconds) -> Unix seconds, 0 for none."""
    if not value:
        return 0
    if str(value).isdigit():
        return int(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def snooze_now(ads, schedule, now, recent_chat):
    """True when Twitch's own scheduled ad is about to hit while chat is busy."""
    if not ads["snooze_when_busy"] or not schedule:
        return False
    due = stamp(schedule.get("next_ad_at"))
    if not due or due - now > 120 or due < now:
        return False
    if recent_chat <= ads["quiet_max"]:
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
    save_state(kind, {"refresh_token": token, "sealed": sealed_id(kind)})


def save_state(key, value):
    state = read_state()
    state[key] = value
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
        self.game = ""

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
        self.broadcaster, self.channel = them[0]["id"], channel
        log.info("Twitch: %s login works (%s)", self.kind, self.me["login"])

    async def live_since(self):
        data = (await self.call("GET", "/streams", params={"user_id": self.broadcaster}))["data"]
        if not data:
            return None
        self.game = data[0].get("game_name") or ""
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

    async def vips(self):
        """Twitch logins of the channel's VIPs (owner login with channel:read:vips)."""
        out, after = [], None
        while True:
            params = {"broadcaster_id": self.me["id"], "first": 100}
            if after:
                params["after"] = after
            res = await self.call("GET", "/channels/vips", params=params)
            out += [(v["user_login"], v["user_name"]) for v in res.get("data", [])]
            after = (res.get("pagination") or {}).get("cursor")
            if not after:
                return out

    async def emotes(self):
        """The channel's own emotes (sub tiers, bits, follower); no extra scope."""
        res = await self.call("GET", "/chat/emotes", params={"broadcaster_id": self.me["id"]})
        return res.get("data", [])

    async def rewards(self):
        """The channel point rewards (owner login with channel:manage:redemptions).
        Twitch only lets an app edit rewards it made itself, so each one says
        whether Race Control can change it."""
        mine = await self.call("GET", "/channel_points/custom_rewards", params={
            "broadcaster_id": self.me["id"], "only_manageable_rewards": "true"})
        ours = {r["id"] for r in mine.get("data", [])}
        res = await self.call("GET", "/channel_points/custom_rewards",
                              params={"broadcaster_id": self.me["id"]})
        return [dict(r, ours=r["id"] in ours) for r in res.get("data", [])]

    async def segments(self):
        """The channel's next scheduled streams (empty when there is no schedule)."""
        try:
            return (await self.call("GET", "/schedule", params={
                "broadcaster_id": self.broadcaster, "first": 5}))["data"]["segments"] or []
        except RuntimeError as e:
            if ": 404" in str(e):
                return []
            raise

    async def set_title(self, title):
        """Needs the owner login with channel:manage:broadcast."""
        await self.call("PATCH", "/channels", params={"broadcaster_id": self.broadcaster},
                        json={"title": title[:140]})

    async def snooze(self):
        res = await self.call("POST", "/channels/ads/schedule/snooze",
                              params={"broadcaster_id": self.me["id"]})
        return (res.get("data") or [{}])[0]


# ---------------------------------------------------------------- running on the server

def chat_match(config, text):
    """(timer index, message) for a !command anyone typed or a question a timer
    answers ("ask": regex, answered with "ask_text" if it has one), else None."""
    import re
    word = (text.split() or [""])[0].lower()
    timers = config.get("timers", [])
    if word.startswith("!"):
        for i, t in enumerate(timers):
            if word in t.get("commands", []):
                return i, t.get("ask_text") or t["text"]
        return None
    for i, t in enumerate(timers):
        if t.get("ask") and asks(t["ask"], text):
            return i, t.get("ask_text") or t["text"]
    return None


def asks(ask, text):
    """Whether a chat line is this question. ask is a regex, or {"topic", "cue",
    "not"}: the topic must be there, plus a sign it's a question or interest
    (cue), and nothing that means it's about something else (not)."""
    import re
    if isinstance(ask, str):
        return bool(re.search(ask, text, re.I))
    return (bool(re.search(ask["topic"], text, re.I))
            and (not ask.get("cue") or bool(re.search(ask["cue"], text, re.I)))
            and not (ask.get("not") and re.search(ask["not"], text, re.I)))


REPORTER = None   # async fn(text): the Discord bot posts Twitch status in the staff LOGS channel
FILL = None       # fn(text) -> text: fills {giveaway} in timer texts (bot/giveaway.py)
ON_SUB = None     # async fn({"user", "gift"}): a new sub came in (bot/giveaway.py)
ANNOUNCE = None   # async fn(text): set while running, for messages from the Discord side
OWNER = None      # GreMi's own Twitch login while running (subs, VIPs)
MIRRORS = []      # async fn(text) per other chat (YouTube) that gets the timers and commands too
SAY = None        # async fn(text): post in Twitch chat (set while running)
ROOM = None       # the running Room, so other chats can add their activity and commands
_reported = set()


async def report(text):
    """Logs a problem and, once per distinct line, hands it to REPORTER (staff LOGS).
    Only for things going wrong; routine success lines go to log.info only."""
    log.info("Twitch: %s", text)
    if REPORTER and text not in _reported:
        _reported.add(text)
        try:
            await REPORTER(text)
        except Exception as e:
            log.warning("Twitch report: %s", e)


async def say_everywhere(bot, text):
    if FILL:
        text = FILL(text)
    await bot.say(text)
    for say in MIRRORS:
        try:
            await say(text)
        except Exception as e:
            await report(f"mirror problem: {e}")


class Room:
    """What the listeners saw: chat messages and finished races."""
    def __init__(self):
        self.chat = collections.deque(maxlen=2000)  # times of viewer messages
        self.asked = False                           # !ad from GreMi or a mod
        self.commands = {}                           # timer index -> when a command or question sent it
        self.race_ended = 0.0
        self.chatters = set()                        # who talked this stream
        self.hidden = set()                          # hidden-name drivers already told this stream
        self.events = []                             # (due time, timer index)
        self.live = False
        self.game = None

    def event(self, kind, delay_from=None):
        if self.live:
            queue_event(load_timers(), self.events, kind, delay_from or time.time(), self.game)

    def talked(self, who):
        """A chat message from someone; the first one this stream is a new chatter."""
        if who and who not in self.chatters:
            self.chatters.add(who)
            self.event("new_chatter")

    def fresh(self):
        self.chatters, self.hidden, self.events = set(), set(), []

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
                        log.info("Twitch: listening to #%s chat as %s", bot.channel, bot.me["login"])
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
                        room.talked("tw:" + (ev.get("chatter_user_id") or ""))
                        hit = chat_match(load_timers(), text)
                        if hit:
                            i, message = hit
                            # answered here, not on the next tick, so it lands right away
                            if time.time() - room.commands.get(i, 0) > COMMAND_GAP:
                                room.commands[i] = time.time()
                                asyncio.create_task(answer_later(bot.say, FILL(message) if FILL else message))
                                log.info("Twitch chat: answered %s", text[:30])
                            if text.startswith("!"):
                                continue
                        room.chat.append(time.time())
        except Exception as e:
            await report(f"chat listener problem: {e}")
        await asyncio.sleep(5 if url == EVENTSUB else 0)


async def answer_later(say, text):
    await asyncio.sleep(REPLY_DELAY)
    try:
        await say(text)
    except Exception as e:
        await report(f"answer problem: {e}")


async def listen_subs(owner):
    """New subs (new, Prime and gifted; not resubs) on GreMi's channel, for the
    giveaway. Needs the owner login with channel:read:subscriptions."""
    url = EVENTSUB
    while True:
        try:
            async with owner.session.ws_connect(url, heartbeat=None) as ws:
                url = EVENTSUB
                async for msg in ws:
                    data = json.loads(msg.data)
                    kind = data["metadata"]["message_type"]
                    if kind == "session_welcome":
                        try:
                            await owner.call("POST", "/eventsub/subscriptions", json={
                                "type": "channel.subscribe", "version": "1",
                                "condition": {"broadcaster_user_id": owner.me["id"]},
                                "transport": {"method": "websocket",
                                              "session_id": data["payload"]["session"]["id"]}})
                        except RuntimeError as e:
                            await report(f"can't count subs for the giveaway ({e}); run the Twitch chat "
                                         "workflow with 'login-owner' again")
                            return
                        log.info("Twitch: counting new subs for the giveaway")
                    elif kind == "session_reconnect":
                        url = data["payload"]["session"]["reconnect_url"]
                        break
                    elif kind == "notification":
                        ev = data["payload"]["event"]
                        log.info("Twitch: new sub (%s)", "gift" if ev.get("is_gift") else "sub")
                        if ROOM is not None:
                            ROOM.event("sub")
                        if ON_SUB:
                            await ON_SUB({"user": ev.get("user_login", ""), "gift": bool(ev.get("is_gift"))})
        except Exception as e:
            await report(f"sub listener problem: {e}")
        await asyncio.sleep(5 if url == EVENTSUB else 0)


async def check_hidden(session, room, sha):
    """New drivers with their name hidden in the race file that just came in: the
    "hidden_driver" event, once per driver per stream."""
    try:
        async with session.get(RACE_API.format(sha)) as r:
            files = [f["filename"] for f in (await r.json()).get("files", [])
                     if f["filename"].startswith("races/") and f["filename"].endswith(".json")
                     and f.get("status") == "added"]
        new = set()
        for name in files:
            async with session.get(RACE_RAW.format(name)) as r:
                if r.status == 200:
                    new |= hidden_keys(json.loads(await r.text()))
        if new - room.hidden:
            room.hidden |= new
            room.event("hidden_driver")
            log.info("Twitch: %d new driver(s) with a hidden name", len(new))
    except Exception as e:
        log.warning("Twitch hidden names: %s", e)


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
                            await check_hidden(session, room, sha)
                        last = sha
        except Exception as e:
            log.warning("Twitch race watch: %s", e)
        await asyncio.sleep(RACES_EVERY)


async def load_titles(session):
    """bot/stream_titles.json from main (the race news routine refreshes it), else the local copy."""
    try:
        async with session.get(TITLES_URL) as r:
            if r.status == 200:
                return json.loads(await r.text())
    except Exception as e:
        log.warning("Twitch titles: %s", e)
    try:
        return json.loads(TITLES.read_text())
    except (OSError, ValueError):
        return {}


async def title_next_stream(owner, session, now):
    """Before a scheduled stream, sets a fresh Twitch title: it is the text of
    Twitch's go-live notification."""
    config = await load_titles(session)
    if not config.get("on", True):
        return
    state = read_state().get("titles") or {}
    titled, used = state.get("segments", []), state.get("used", [])
    seg = segment_to_title(await owner.segments(), now, config.get("before_minutes", 60), titled)
    if not seg:
        return
    title = pick_title(config.get("titles", []), used)
    if not title:
        return
    await owner.set_title(title)
    used = [t for t in used if t != title] + [title]
    save_state("titles", {"segments": (titled + [seg["id"]])[-20:], "used": used[-60:]})
    log.info("Twitch: stream title set: %s", title)


async def run(discord_token):
    """Runs next to the Discord bot; never raises."""
    if not SEALED["chat"].exists():
        await report("no chat login yet")
        return
    import aiohttp
    logins = {}
    for kind in SEALED:
        if SEALED[kind].exists():
            try:
                logins[kind] = current_login(kind, discord_token)
            except Exception as e:
                await report(f"can't open the {kind} login ({type(e).__name__}); "
                             "it may be sealed with a different Discord bot token")
    if "chat" not in logins:
        return
    global ROOM, SAY, ANNOUNCE, OWNER
    room = ROOM = Room()
    sent, last_any, was_live, since = {}, 0.0, None, None
    last_race_ad, lobby_done, schedule, checked_live, checked_ads = 0.0, 0.0, None, 0.0, 0.0
    checked_title = 0.0
    spent, counted = collections.Counter(), set()  # ad seconds per stream hour
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        bot = Twitch("chat", logins["chat"], session)
        SAY = bot.say
        owner = OWNER = Twitch("owner", logins["owner"], session) if "owner" in logins else None
        ANNOUNCE = lambda text: say_everywhere(bot, text)
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
                    tasks.append(asyncio.create_task(listen_subs(owner)))
                now = time.time()
                if now - checked_live >= LIVE_EVERY:
                    since, checked_live = await bot.live_since(), now
                if was_live is not None and since is None:  # stream ended
                    room.events = []
                    queue_event(config, room.events, "stream_end", now, bot.game)
                    while room.events:
                        i = due_event(config, room.events, {}, 0.0, now, bot.game)
                        if i is None:
                            break
                        await say_everywhere(bot, config["timers"][i]["text"])
                if since != was_live:  # stream started or ended: start fresh
                    room.fresh()
                    room.live = since is not None
                    sent, last_any, was_live = {}, 0.0, since
                    last_race_ad, lobby_done, room.race_ended, room.asked = 0.0, 0.0, 0.0, False
                    spent, counted, schedule = collections.Counter(), set(), None
                minutes = None if since is None else (now - since) / 60

                room.game = bot.game
                for i, t in room.commands.items():  # an answered !command counts as that timer going out
                    if since is not None and t > since:
                        sent[i] = max(sent.get(i, 0.0), t)
                        last_any = max(last_any, t)
                i = due_event(config, room.events, sent, last_any, now, bot.game)
                if i is None:
                    i = next_timer(config, minutes, sent, last_any, now,
                                   room.since(last_any or (since or now)), bot.game)
                if i is not None:
                    await say_everywhere(bot, config["timers"][i]["text"])
                    sent[i] = last_any = now
                    log.info("Twitch chat: sent timer %s", config["timers"][i].get("id", i))

                if owner and since is None and now - checked_title >= TITLE_EVERY:
                    checked_title = now
                    try:
                        await title_next_stream(owner, session, now)
                    except Exception as e:
                        if ": 401" in str(e) or ": 403" in str(e):
                            await report("can't set the stream title yet: run the Twitch chat "
                                         "workflow with 'login-owner' again")
                        else:
                            await report(f"title problem: {e}")

                if owner:
                    recent = room.since(now - ads["quiet_minutes"] * 60)
                    if since is not None and now - checked_ads >= ADS_EVERY:
                        schedule, checked_ads = await owner.ad_schedule(), now
                        seen = stamp(schedule.get("last_ad_at"))
                        # Twitch's own scheduled ads count toward the hour too
                        if seen > since and all(abs(seen - t) > 60 for t in counted):
                            counted.add(seen)
                            spent[int((seen - since) // 3600)] += int(schedule.get("duration") or 0)
                            room.event("ad")
                        if snooze_now(ads, schedule, now, recent):
                            await owner.snooze()
                            log.info("Twitch: snoozed the scheduled ad (chat is busy)")
                    hour = 0 if since is None else int((now - since) // 3600)
                    plan = plan_ad(ads, now, minutes, last_race_ad, room.race_ended, lobby_done,
                                   room.asked, recent, spent[hour],
                                   0 if since is None else (now - since) % 3600 / 60)
                    room.asked = False
                    if plan:
                        why, length = plan
                        # marked first, so a refused ad isn't retried every few seconds
                        if why == "race":
                            last_race_ad = now
                        elif why == "lobby":
                            lobby_done = room.race_ended
                        counted.add(now)
                        notice = ads["notice_race"] if why == "race" else ads["notice_lobby"]
                        if notice:
                            await bot.say(notice.format(length=length))
                        res = await owner.run_ad(length)
                        room.event("ad")
                        spent[hour] += int(res.get("length") or length)
                        log.info("Twitch: ad (%s) %s s, %s s this hour", why, length, spent[hour])
            except Exception as e:
                await report(f"problem: {e}")
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
    if not stamp(value):
        return "none"
    t = dt.datetime.fromtimestamp(stamp(value), dt.timezone.utc)
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
    elif len(argv) == 2 and argv[1] == "emotes":
        for e in sorted(asyncio.run(once("owner", token, lambda c: c.emotes())),
                        key=lambda e: (e.get("emote_type", ""), e.get("tier", ""), e["name"])):
            kind = {"subscriptions": f"sub tier {int(e.get('tier') or 1000) // 1000}",
                    "bitstier": "bits", "follower": "follower"}.get(e.get("emote_type"), e.get("emote_type"))
            print(f"{e['name']} | {kind} | {'animated' if 'animated' in e.get('format', []) else 'still'} | "
                  f"https://static-cdn.jtvnw.net/emoticons/v2/{e['id']}/default/dark/3.0")
    elif len(argv) == 2 and argv[1] == "rewards":
        for r in sorted(asyncio.run(once("owner", token, lambda c: c.rewards())), key=lambda r: r["cost"]):
            limits = [f"{k} {v}" for k, v in (
                ("per stream", (r.get("max_per_stream_setting") or {}).get("max_per_stream")
                 if (r.get("max_per_stream_setting") or {}).get("is_enabled") else None),
                ("per viewer", (r.get("max_per_user_per_stream_setting") or {}).get("max_per_user_per_stream")
                 if (r.get("max_per_user_per_stream_setting") or {}).get("is_enabled") else None),
                ("cooldown s", (r.get("global_cooldown_setting") or {}).get("global_cooldown_seconds")
                 if (r.get("global_cooldown_setting") or {}).get("is_enabled") else None)) if v]
            print(f"{r['cost']:>7} | {r['title']} | {'on' if r['is_enabled'] else 'OFF'}"
                  f"{' paused' if r.get('is_paused') else ''} | "
                  f"{'needs text' if r.get('is_user_input_required') else 'no text'} | "
                  f"{'auto-done' if r.get('should_redemptions_skip_request_queue') else 'queue'} | "
                  f"{', '.join(limits) or 'no limits'} | {'Race Control can edit' if r['ours'] else 'made elsewhere'} | "
                  f"{(r.get('prompt') or '').replace(chr(10), ' ')}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
