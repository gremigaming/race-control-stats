"""Updates the stat voice channels (members, Twitch live status and followers,
YouTube, TikTok).

Runs on a schedule via GitHub Actions. Only renames a channel when its
value actually changed, to stay well inside Discord's rename rate limit.
One failing data source never blocks the others.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DISCORD = "https://discord.com/api/v10"
UA = "DiscordBot (https://github.com/race-control-stats, 1.0)"

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = os.environ["GUILD_ID"]
TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
TWITCH_LOGIN = os.environ.get("TWITCH_LOGIN", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
YOUTUBE_CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "")
TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
TIKTOK_REFRESH_TOKEN = os.environ.get("TIKTOK_REFRESH_TOKEN", "")
# Where to write TikTok's new refresh token when it changes, so the workflow can save it
TIKTOK_REFRESH_TOKEN_OUT = os.environ.get("TIKTOK_REFRESH_TOKEN_OUT", "")
# Role the server owner gets while live on Twitch (matched by name, empty turns it off)
LIVE_ROLE_NAME = os.environ.get("LIVE_ROLE_NAME", "LIVE RIGHT NOW")


def http(method, url, headers=None, data=None, form=False):
    headers = dict(headers or {})
    headers.setdefault("User-Agent", UA)
    safe_url = url.split("?")[0]  # never print query strings (they can hold keys)
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    for attempt in range(6):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2
                try:
                    wait = float(json.loads(e.read()).get("retry_after", 2))
                except Exception:
                    pass
                time.sleep(wait + 0.2)
                continue
            if e.code >= 500:
                time.sleep(2)
                continue
            raise RuntimeError(f"{method} {safe_url} failed: {e.code} {e.read()[:200]!r}")
        except urllib.error.URLError:
            time.sleep(2)
    raise RuntimeError(f"{method} {safe_url} failed after retries")


def discord(method, path, data=None):
    return http(method, DISCORD + path, {"Authorization": f"Bot {DISCORD_TOKEN}"}, data)


def bold_caps(text):
    out = []
    for ch in text.upper():
        if "A" <= ch <= "Z":
            out.append(chr(0x1D5D4 + ord(ch) - ord("A")))
        elif "0" <= ch <= "9":
            out.append(chr(0x1D7EC + ord(ch) - ord("0")))
        else:
            out.append(ch)
    return "".join(out)


# ---------- data sources ----------
_once_cache = {}


def once(fn):
    """Asks a data source only once per run, so the stat channels and the
    socials board share one answer (TikTok must not refresh its login twice).
    Errors are remembered too, so a failing source fails the same way twice."""
    def wrapper():
        if fn.__name__ not in _once_cache:
            try:
                _once_cache[fn.__name__] = (fn(), None)
            except Exception as e:
                _once_cache[fn.__name__] = (None, e)
        value, error = _once_cache[fn.__name__]
        if error is not None:
            raise error
        return value
    wrapper.__name__ = fn.__name__
    return wrapper


def member_count():
    guild = discord("GET", f"/guilds/{GUILD_ID}?with_counts=true")
    return guild["approximate_member_count"]


_twitch_cache = {}


def twitch_headers():
    """App-token headers for the Twitch API, or None if Twitch isn't configured."""
    if not (TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET and TWITCH_LOGIN):
        return None
    if "headers" not in _twitch_cache:
        token = http("POST", "https://id.twitch.tv/oauth2/token", data={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        }, form=True)["access_token"]
        _twitch_cache["headers"] = {"Client-Id": TWITCH_CLIENT_ID,
                                    "Authorization": f"Bearer {token}"}
    return _twitch_cache["headers"]


def twitch_is_live():
    """True/False, or None if Twitch isn't configured. Asked once per run."""
    if "live" not in _twitch_cache:
        headers = twitch_headers()
        if headers is None:
            return None
        streams = http("GET",
                       "https://api.twitch.tv/helix/streams?user_login="
                       + urllib.parse.quote(TWITCH_LOGIN), headers)
        data = streams.get("data", [])
        _twitch_cache["live"] = len(data) > 0
        _twitch_cache["stream"] = data[0] if data else None
    return _twitch_cache["live"]


def twitch_stream():
    """The live stream (title, game_name, viewer_count, ...), or None when offline."""
    twitch_is_live()
    return _twitch_cache.get("stream")


@once
def twitch_followers():
    """Follower total, or None if Twitch isn't configured.

    Only the total is read, which Twitch returns without special permissions.
    """
    headers = twitch_headers()
    if headers is None:
        return None
    users = http("GET",
                 "https://api.twitch.tv/helix/users?login="
                 + urllib.parse.quote(TWITCH_LOGIN), headers).get("data", [])
    if not users:
        raise RuntimeError("Twitch user not found, check TWITCH_LOGIN")
    result = http("GET",
                  "https://api.twitch.tv/helix/channels/followers?first=1&broadcaster_id="
                  + users[0]["id"], headers)
    return int(result["total"])


@once
def youtube_subscribers():
    """Subscriber count, or None if not configured or the channel hides it.

    YouTube rounds this to 3 significant figures above 1,000 subscribers.
    """
    if not (YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID):
        return None
    url = ("https://www.googleapis.com/youtube/v3/channels?part=statistics&id="
           + urllib.parse.quote(YOUTUBE_CHANNEL_ID)
           + "&key=" + urllib.parse.quote(YOUTUBE_API_KEY))
    items = http("GET", url).get("items", [])
    if not items:
        raise RuntimeError("YouTube channel not found, check YOUTUBE_CHANNEL_ID")
    stats = items[0]["statistics"]
    if stats.get("hiddenSubscriberCount"):
        return None
    return int(stats["subscriberCount"])


# The TikTok access token of this run, so the socials board can read the latest video
_tiktok_access = {}

TIKTOK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiktok.json")
TIKTOK_API = "https://open.tiktokapis.com/v2"


@once
def tiktok_followers():
    """Follower count from the TikTok API if it's set up, else from tiktok.json.

    Returns None when neither is set up.
    """
    if TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET and TIKTOK_REFRESH_TOKEN:
        return tiktok_api_followers()
    return tiktok_file_followers()


def tiktok_api_followers():
    """Reads follower_count with a fresh access token (they last 24 hours).

    TikTok may hand back a new refresh token. It is written to
    TIKTOK_REFRESH_TOKEN_OUT so the workflow can store it as the new secret.
    """
    token = http("POST", TIKTOK_API + "/oauth/token/", data={
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": TIKTOK_REFRESH_TOKEN,
    }, form=True)
    if "access_token" not in token:
        raise RuntimeError(f"TikTok login expired or invalid ({token.get('error', 'unknown error')}),"
                           " run the TikTok login workflow again")
    new_refresh = token.get("refresh_token")
    if new_refresh and new_refresh != TIKTOK_REFRESH_TOKEN and TIKTOK_REFRESH_TOKEN_OUT:
        with open(TIKTOK_REFRESH_TOKEN_OUT, "w", encoding="utf-8") as f:
            f.write(new_refresh)
    _tiktok_access["token"] = token["access_token"]
    info = http("GET", TIKTOK_API + "/user/info/?fields=follower_count",
                {"Authorization": f"Bearer {token['access_token']}"})
    error = info.get("error", {})
    if error.get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok user info failed: {error.get('code')}")
    return int(info["data"]["user"]["follower_count"])


def tiktok_file_followers():
    """Follower count from the hand edited tiktok.json, or None if not set.

    Leave "followers" as null (or delete the file) to skip TikTok.
    """
    try:
        with open(TIKTOK_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except ValueError:
        raise RuntimeError("tiktok.json is not valid JSON")
    value = data.get("followers") if isinstance(data, dict) else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError('tiktok.json "followers" must be a whole number, like 1234')
    return value


# Later: race stats


# ---------- channel handling ----------
def find_stat_channels():
    """Voice channels inside the STATS category, keyed by their leading emoji."""
    channels = discord("GET", f"/guilds/{GUILD_ID}/channels")
    marker = bold_caps("stats")
    cats = [c for c in channels if c["type"] == 4 and marker in c["name"]]
    if not cats:
        raise RuntimeError("No STATS category found")
    cat_id = cats[0]["id"]
    return {c["name"].split("\u2503")[0]: c
            for c in channels if c["type"] == 2 and c.get("parent_id") == cat_id}


def set_name(channel, emoji, label):
    wanted = f"{emoji}\u2503{bold_caps(label)}"
    if channel["name"] == wanted:
        print(f"unchanged: {label}")
        return
    discord("PATCH", f"/channels/{channel['id']}", {"name": wanted})
    print(f"updated:   {label}")


def update_members(stat):
    ch = stat.get("\U0001F465")
    if ch:
        set_name(ch, "\U0001F465", f"members: {member_count():,}")


def update_status(stat):
    ch = stat.get("\U0001F534")
    live = twitch_is_live()
    if ch and live is not None:
        set_name(ch, "\U0001F534", "status: live now" if live else "status: offline")


def update_live_role(stat):
    """Gives the server owner the live role while live on Twitch, removes it after."""
    live = twitch_is_live()
    if not LIVE_ROLE_NAME or live is None:
        return
    role = next((r for r in discord("GET", f"/guilds/{GUILD_ID}/roles")
                 if r["name"] == LIVE_ROLE_NAME), None)
    if role is None:
        raise RuntimeError(f"no role named {LIVE_ROLE_NAME!r}")
    owner = discord("GET", f"/guilds/{GUILD_ID}")["owner_id"]
    has = role["id"] in discord("GET", f"/guilds/{GUILD_ID}/members/{owner}")["roles"]
    if live == has:
        print(f"unchanged: live role {'on' if has else 'off'}")
        return
    discord("PUT" if live else "DELETE",
            f"/guilds/{GUILD_ID}/members/{owner}/roles/{role['id']}")
    print(f"updated:   live role {'added' if live else 'removed'}")


def update_twitch(stat):
    ch = stat.get("\U0001F7E3")
    followers = twitch_followers()
    if ch and followers is not None:
        set_name(ch, "\U0001F7E3", f"twitch: {followers:,}")


def update_youtube(stat):
    ch = stat.get("\u25B6\uFE0F")
    subs = youtube_subscribers()
    if ch and subs is not None:
        set_name(ch, "\u25B6\uFE0F", f"youtube: {subs:,}")


def update_tiktok(stat):
    ch = stat.get("\U0001F3B5")
    followers = tiktok_followers()
    if ch and followers is not None:
        set_name(ch, "\U0001F3B5", f"tiktok: {followers:,}")


def update_socials_board(stat):
    import socials_board
    import sys
    # this module itself, also when it runs as __main__, so both share one set of answers
    socials_board.update(sys.modules[__name__])


def update_stream_schedule(stat):
    import stream_schedule
    import sys
    stream_schedule.update(sys.modules[__name__])


def main():
    stat = find_stat_channels()
    failed = False
    for task in (update_members, update_status, update_live_role, update_twitch,
                 update_youtube, update_tiktok, update_socials_board,
                 update_stream_schedule):
        try:
            task(stat)
        except Exception as e:
            failed = True
            print(f"FAILED {task.__name__}: {e}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
