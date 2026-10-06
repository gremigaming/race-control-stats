"""The "Team Radio" board in the Socials channel.

One Race Control message with GreMi's follower counts, link buttons and the
Twitch live status. The stats workflow edits that same message every run, and
only when something on it changed.

The workflow never posts a new board by itself. The first post is done once by
hand (after the owner approved it):
    python3 socials_board.py --preview   prints the message, sends nothing
    python3 socials_board.py --post      posts the board in the Socials channel
"""
import os
import sys
from datetime import datetime, timezone

SOCIALS_CHANNEL_ID = os.environ.get("SOCIALS_CHANNEL_ID", "")
FOOTER = "Race Control \u00b7 updated automatically"

RED = 0xC8102E      # racing red while offline
PURPLE = 0x9146FF   # Twitch purple while live

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


def count(value, word):
    return f"**{value:,}**\n{word}" if value is not None else f"**\u2014**\n{word}"


def server_icon(stats):
    """The server icon's address, shown next to the name at the top."""
    guild = safe(lambda: stats.discord("GET", f"/guilds/{stats.GUILD_ID}"))
    if guild and guild.get("icon"):
        return f"https://cdn.discordapp.com/icons/{guild['id']}/{guild['icon']}.png?size=128"
    return None


def build(stats):
    """The board message (embed plus link buttons), from update_stats' sources."""
    twitch = safe(stats.twitch_followers)
    youtube = safe(stats.youtube_subscribers)
    tiktok = safe(stats.tiktok_followers)
    stream = safe(stats.twitch_stream)

    if stream:
        game = stream.get("game_name") or "Sim racing"
        viewers = stream.get("viewer_count")
        watching = f" \u00b7 {viewers:,} watching" if viewers is not None else ""
        status = (f"\U0001F534 **Live now** \u00b7 {game}{watching}\n"
                  f"> {stream.get('title', '').strip()}")
    else:
        status = ("\u26AB **Offline**\n"
                  "Follow on Twitch to get notified when the next stream starts.")

    fields = [
        {"name": f"{emoji_text('twitch')} Twitch", "value": count(twitch, "followers"),
         "inline": True},
        {"name": f"{emoji_text('youtube')} YouTube", "value": count(youtube, "subscribers"),
         "inline": True},
        {"name": f"{emoji_text('tiktok')} TikTok", "value": count(tiktok, "followers"),
         "inline": True},
    ]
    grid = [v for v in (twitch, youtube, tiktok) if v is not None]
    footer = FOOTER + (f" \u00b7 {sum(grid):,} total" if grid else "")

    embed = {
        "author": {"name": "GreMi_Gaming"},
        "title": "Official channels",
        "description": status,
        "color": PURPLE if stream else RED,
        "fields": fields,
        "footer": {"text": footer},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    icon = server_icon(stats)
    if icon:
        embed["author"]["icon_url"] = icon
    if stream and stream.get("thumbnail_url"):
        # started_at keeps one picture per stream instead of Discord's cached first one
        embed["image"] = {"url": stream["thumbnail_url"]
                          .replace("{width}", "1280").replace("{height}", "720")
                          + "?s=" + "".join(ch for ch in stream.get("started_at", "")
                                            if ch.isdigit())}

    buttons = [("Watch live" if stream else "Twitch", TWITCH_URL, "twitch"),
               ("YouTube", youtube_url(), "youtube"),
               ("TikTok", TIKTOK_URL, "tiktok")]
    components = [{"type": 1, "components": [
        {"type": 2, "style": 5, "label": label, "url": url, "emoji": EMOJI[key]}
        for label, url, key in buttons if url]}]
    return {"embeds": [embed], "components": components,
            "allowed_mentions": {"parse": []}}


def signature(message):
    """What a reader sees, without the timestamp, so unchanged boards aren't edited."""
    embed = (message.get("embeds") or [{}])[0]
    fields = tuple((f.get("name"), f.get("value")) for f in embed.get("fields", []))
    buttons = tuple((b.get("label"), b.get("url"))
                    for row in message.get("components", [])
                    for b in row.get("components", []))
    return (embed.get("title"), embed.get("description"), embed.get("color"), fields,
            (embed.get("image") or {}).get("url"), (embed.get("footer") or {}).get("text"),
            (embed.get("author") or {}).get("icon_url"), buttons)


def find_board(stats):
    """The board message in the Socials channel, or None if it isn't posted yet."""
    me = stats.discord("GET", "/users/@me")["id"]
    for msg in stats.discord("GET", f"/channels/{SOCIALS_CHANNEL_ID}/messages?limit=50"):
        footer = ((msg.get("embeds") or [{}])[0].get("footer") or {}).get("text", "")
        if msg["author"]["id"] == me and footer.startswith(FOOTER):
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
