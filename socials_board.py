"""The "Team Radio" board in the Socials channel.

One Race Control message with a coloured card per platform (follower counts,
live status, latest stream and video previews) and link buttons. The stats workflow edits that same message every run, and
only when something on it changed.

The workflow never posts a new board by itself. The first post is done once by
hand (after the owner approved it):
    python3 socials_board.py --preview   prints the message, sends nothing
    python3 socials_board.py --post      posts the board in the Socials channel
"""
import os
import sys

SOCIALS_CHANNEL_ID = os.environ.get("SOCIALS_CHANNEL_ID", "")
TITLE = "Official channels"
HEADER = f"## \U0001F3C1 GreMi_Gaming \u00b7 {TITLE}"

# Each platform's card gets its own brand colour on the left edge
TWITCH_PURPLE = 0x9146FF
YOUTUBE_RED = 0xFF0033
TIKTOK_CYAN = 0x25F4EE

TWITCH_URL = "https://twitch.tv/GreMi_Gaming"
TIKTOK_URL = "https://www.tiktok.com/@ttv.gremi_gaming"

# Custom emojis on the GreMi_Gaming server
EMOJI = {
    "twitch": {"id": "1085833562474938438", "name": "twitch"},
    "youtube": {"id": "1508733155883094066", "name": "Youtube_logo"},
    "tiktok": {"id": "1097447464010788864", "name": "TikTok"},
}


def emoji_text(key):
    e = EMOJI[key]
    return f"<:{e['name']}:{e['id']}>"


def youtube_url():
    override = os.environ.get("YOUTUBE_URL", "")
    if override:
        return override
    channel = os.environ.get("YOUTUBE_CHANNEL_ID", "")
    return f"https://www.youtube.com/channel/{channel}" if channel else ""


def safe(fn):
    """A data source's value, or None if it isn't set up or fails right now.
    The stat channel step for that source already reports the failure."""
    try:
        return fn()
    except Exception as e:
        print(f"socials board: {fn.__name__} unavailable ({e})")
        return None


def number(value, word):
    return f"**{value:,}** {word}" if value is not None else f"**\u2014** {word}"


def short(text, limit=90):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


# ---------- extra details for the cards ----------
def twitch_details(stats):
    """Profile picture, offline banner and the latest past broadcast from Twitch."""
    headers = stats.twitch_headers()
    if headers is None:
        return None
    import urllib.parse
    users = stats.http("GET", "https://api.twitch.tv/helix/users?login="
                       + urllib.parse.quote(stats.TWITCH_LOGIN), headers).get("data", [])
    if not users:
        return None
    user = users[0]
    details = {"avatar": user.get("profile_image_url") or None,
               "banner": user.get("offline_image_url") or None, "video": None}
    videos = stats.http("GET", "https://api.twitch.tv/helix/videos?first=1&type=archive&user_id="
                        + user["id"], headers).get("data", [])
    if videos:
        v = videos[0]
        thumb = (v.get("thumbnail_url") or "").replace("%{width}", "1280") \
            .replace("%{height}", "720")
        # Twitch shows a placeholder picture while a broadcast is still processing
        details["video"] = {"title": v.get("title", ""), "url": v.get("url", ""),
                            "thumb": thumb if "_404/" not in thumb else ""}
    return details


def youtube_details(stats):
    """Channel picture and the newest upload from YouTube."""
    if not (stats.YOUTUBE_API_KEY and stats.YOUTUBE_CHANNEL_ID):
        return None
    import urllib.parse
    key = "&key=" + urllib.parse.quote(stats.YOUTUBE_API_KEY)
    items = stats.http("GET", "https://www.googleapis.com/youtube/v3/channels"
                       "?part=snippet,contentDetails&id="
                       + urllib.parse.quote(stats.YOUTUBE_CHANNEL_ID) + key).get("items", [])
    if not items:
        return None
    channel = items[0]
    thumbs = channel.get("snippet", {}).get("thumbnails", {})
    details = {"avatar": (thumbs.get("high") or thumbs.get("default") or {}).get("url"),
               "video": None}
    uploads = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if uploads:
        latest = stats.http("GET", "https://www.googleapis.com/youtube/v3/playlistItems"
                            "?part=snippet&maxResults=1&playlistId="
                            + urllib.parse.quote(uploads) + key).get("items", [])
        if latest:
            sn = latest[0]["snippet"]
            vid = sn.get("resourceId", {}).get("videoId", "")
            t = sn.get("thumbnails", {})
            best = next((t[k]["url"] for k in ("maxres", "standard", "high", "medium")
                         if k in t), "")
            details["video"] = {"title": sn.get("title", ""),
                                "url": f"https://www.youtube.com/watch?v={vid}", "thumb": best}
    return details


# ---------- the cards ----------
def twitch_card(stats):
    followers = safe(stats.twitch_followers)
    stream = safe(stats.twitch_stream)
    details = safe(lambda: twitch_details(stats)) or {}
    lines = [number(followers, "followers")]
    image = None
    if stream:
        viewers = stream.get("viewer_count")
        watching = f" \u00b7 {viewers:,} watching" if viewers is not None else ""
        lines.append(f"\U0001F534 **Live now** \u00b7 {stream.get('game_name') or 'Sim racing'}"
                     f"{watching}\n> {short(stream.get('title'))}")
        if stream.get("thumbnail_url"):
            # started_at keeps one picture per stream instead of Discord's cached first one
            image = (stream["thumbnail_url"].replace("{width}", "1280")
                     .replace("{height}", "720") + "?s="
                     + "".join(ch for ch in stream.get("started_at", "") if ch.isdigit()))
    else:
        lines.append("\u26AB Offline \u00b7 follow to get notified when the next stream starts")
        video = details.get("video")
        if video and video.get("url"):
            lines.append(f"**Last stream:** [{short(video['title'])}]({video['url']})")
            image = video.get("thumb") or None
        image = image or details.get("banner")
    card = {"title": "Twitch", "url": TWITCH_URL, "color": TWITCH_PURPLE,
            "description": "\n".join(lines)}
    if details.get("avatar"):
        card["thumbnail"] = {"url": details["avatar"]}
    if image:
        card["image"] = {"url": image}
    return card, bool(stream)


def youtube_card(stats):
    subs = safe(stats.youtube_subscribers)
    details = safe(lambda: youtube_details(stats)) or {}
    lines = [number(subs, "subscribers")]
    card = {"title": "YouTube", "color": YOUTUBE_RED}
    if youtube_url():
        card["url"] = youtube_url()
    video = details.get("video")
    if video:
        lines.append(f"**Latest video:** [{short(video['title'])}]({video['url']})")
        if video.get("thumb"):
            card["image"] = {"url": video["thumb"]}
    card["description"] = "\n".join(lines)
    if details.get("avatar"):
        card["thumbnail"] = {"url": details["avatar"]}
    return card


def tiktok_card(stats):
    followers = safe(stats.tiktok_followers)
    return {"title": "TikTok", "url": TIKTOK_URL, "color": TIKTOK_CYAN,
            "description": number(followers, "followers")
            + "\nShort clips and race highlights"}


def build(stats):
    """The board message: a header line, one coloured card per platform, link buttons."""
    twitch, live = twitch_card(stats)
    cards = [twitch, youtube_card(stats), tiktok_card(stats)]
    for card, key in zip(cards, ("twitch", "youtube", "tiktok")):
        card["description"] = f"{emoji_text(key)} " + card["description"]
    buttons = [("Watch live" if live else "Twitch", TWITCH_URL, "twitch"),
               ("YouTube", youtube_url(), "youtube"),
               ("TikTok", TIKTOK_URL, "tiktok")]
    components = [{"type": 1, "components": [
        {"type": 2, "style": 5, "label": label, "url": url, "emoji": EMOJI[key]}
        for label, url, key in buttons if url]}]
    return {"content": HEADER, "embeds": cards, "components": components,
            "allowed_mentions": {"parse": []}}


def signature(message):
    """What a reader sees, so unchanged boards aren't edited."""
    def card(e):
        return (e.get("title"), e.get("url"), e.get("description"), e.get("color"),
                (e.get("image") or {}).get("url"), (e.get("thumbnail") or {}).get("url"),
                (e.get("author") or {}).get("name"),
                tuple((f.get("name"), f.get("value")) for f in e.get("fields", [])),
                (e.get("footer") or {}).get("text"), e.get("timestamp") is not None)
    buttons = tuple((b.get("label"), b.get("url"))
                    for row in message.get("components", [])
                    for b in row.get("components", []))
    return (message.get("content", ""), tuple(card(e) for e in message.get("embeds", [])),
            buttons)


def find_board(stats):
    """The board message in the Socials channel, or None if it isn't posted yet."""
    me = stats.discord("GET", "/users/@me")["id"]
    for msg in stats.discord("GET", f"/channels/{SOCIALS_CHANNEL_ID}/messages?limit=50"):
        title = (msg.get("embeds") or [{}])[0].get("title", "")
        if msg["author"]["id"] == me and (msg.get("content") == HEADER or title == TITLE):
            return msg
    return None


def update(stats):
    """Edits the board when something changed. Called by update_stats.main()."""
    if not SOCIALS_CHANNEL_ID:
        return
    board = find_board(stats)
    if board is None:
        print("socials board: not posted yet, skipping")
        return
    wanted = build(stats)
    if signature(board) == signature(wanted):
        print("unchanged: socials board")
        return
    stats.discord("PATCH", f"/channels/{SOCIALS_CHANNEL_ID}/messages/{board['id']}", wanted)
    print("updated:   socials board")


def main(argv):
    import json
    import update_stats
    message = build(update_stats)
    if "--post" in argv:
        if not SOCIALS_CHANNEL_ID:
            raise SystemExit("Set SOCIALS_CHANNEL_ID first")
        if find_board(update_stats):
            raise SystemExit("The board is already posted")
        msg = update_stats.discord("POST", f"/channels/{SOCIALS_CHANNEL_ID}/messages", message)
        print(f"posted socials board {msg['id']}")
    else:
        print(json.dumps(message, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
