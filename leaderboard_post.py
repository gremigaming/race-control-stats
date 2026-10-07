"""Posts the race stats leaderboard picture in Discord.

The race stats site (gremigaming/race-stats) renders this month's top 15 as
discord/leaderboard.png on every build, with leaderboard.json next to it. Its
"hash" only changes when the standings do. One message per month: it is edited
while the month runs, and a new month gets a new message once it has a race,
so last month's final standings stay in the channel.
"""
import json
import os
import urllib.request

LEADERBOARD_CHANNEL_ID = os.environ.get("LEADERBOARD_CHANNEL_ID", "")
SITE = "https://gremigaming.github.io/race-stats/"
FILE_PREFIX = "leaderboard-"


def fetch(stats, path):
    req = urllib.request.Request(SITE + path, headers={"User-Agent": stats.UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def find_posts(stats):
    """Race Control's leaderboard posts in the channel, newest first."""
    me = stats.discord("GET", "/users/@me")["id"]
    msgs = stats.discord("GET", f"/channels/{LEADERBOARD_CHANNEL_ID}/messages?limit=50")
    return [m for m in msgs if m["author"]["id"] == me
            and any(a.get("filename", "").startswith(FILE_PREFIX)
                    for a in m.get("attachments", []))]


def month_title(month):
    import datetime
    year, mon = (int(x) for x in month.split("-"))
    return datetime.date(year, mon, 1).strftime("%B %Y")


def update(stats):
    if not LEADERBOARD_CHANNEL_ID:
        return
    import socials_board
    card = json.loads(fetch(stats, f"discord/leaderboard.json?{os.urandom(4).hex()}"))
    month, digest = card["month"], card["hash"]
    name = f"{FILE_PREFIX}{month}-{digest}.png"
    posts = find_posts(stats)
    this_month = next((m for m in posts if any(
        a["filename"].startswith(f"{FILE_PREFIX}{month}-") for a in m["attachments"])), None)
    if this_month and any(a["filename"] == name for a in this_month["attachments"]):
        print("unchanged: leaderboard picture")
        return
    if not this_month and not card.get("races"):
        print("leaderboard: no races this month yet, keeping last month's post")
        return
    picture = fetch(stats, f"discord/leaderboard.png?{digest}")
    message = {
        "content": f"## 🏆 GreMi Gang Leaderboard · {month_title(month)}\n"
                   f"-# Updates after every race · find your own stats at <{SITE}>",
        "attachments": [{"id": 0, "filename": name}],
        "allowed_mentions": {"parse": []},
    }
    if this_month:
        socials_board.send(stats, "PATCH",
                           f"/channels/{LEADERBOARD_CHANNEL_ID}/messages/{this_month['id']}",
                           message, [(name, picture)])
        print("updated:   leaderboard picture")
        return
    msg = socials_board.send(stats, "POST", f"/channels/{LEADERBOARD_CHANNEL_ID}/messages",
                             message, [(name, picture)])
    print(f"posted:    leaderboard picture {msg.get('id')}")
