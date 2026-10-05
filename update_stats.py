"""Updates the stat voice channels (member count, Twitch live status).

Runs on a schedule via GitHub Actions. Only renames a channel when its
value actually changed, to stay well inside Discord's rename rate limit.
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


def http(method, url, headers=None, data=None, form=False):
    headers = dict(headers or {})
    headers.setdefault("User-Agent", UA)
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
            raise RuntimeError(f"{method} {url} failed: {e.code} {e.read()[:200]!r}")
        except urllib.error.URLError:
            time.sleep(2)
    raise RuntimeError(f"{method} {url} failed after retries")


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
def member_count():
    guild = discord("GET", f"/guilds/{GUILD_ID}?with_counts=true")
    return guild["approximate_member_count"]


def twitch_is_live():
    """True/False, or None if Twitch isn't configured."""
    if not (TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET and TWITCH_LOGIN):
        return None
    token = http("POST", "https://id.twitch.tv/oauth2/token", data={
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials",
    }, form=True)["access_token"]
    streams = http("GET",
                   "https://api.twitch.tv/helix/streams?user_login="
                   + urllib.parse.quote(TWITCH_LOGIN),
                   {"Client-Id": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"})
    return len(streams.get("data", [])) > 0


# Stage 2 (later): twitch_followers(), youtube_subscribers(), tiktok_followers()


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


def main():
    stat = find_stat_channels()

    members_ch = stat.get("\U0001F465")
    if members_ch:
        set_name(members_ch, "\U0001F465", f"members: {member_count():,}")

    live = twitch_is_live()
    status_ch = stat.get("\U0001F534")
    if status_ch and live is not None:
        set_name(status_ch, "\U0001F534", "status: live now" if live else "status: offline")


if __name__ == "__main__":
    main()
