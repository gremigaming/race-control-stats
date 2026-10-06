"""The socials board in the Socials channel.

One Race Control message: a header line, a picture with Twitch, YouTube and
TikTok side by side (each in its own colour, with its follower number and a
small preview of the latest stream or video) and link buttons. The picture is
drawn by board_image.py. The stats workflow edits that same message every run,
and only when something on it changed.

The workflow never posts a new board by itself. The first post is done once by
hand (after the owner approved it):
    python3 socials_board.py --preview board.png   draws the picture, sends nothing
    python3 socials_board.py --post                posts the board in the Socials channel
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SOCIALS_CHANNEL_ID = os.environ.get("SOCIALS_CHANNEL_ID", "")
TITLE = "Official channels"
HEADER = f"## \U0001F3C1 GreMi_Gaming · {TITLE}"
FILE_PREFIX = "socials-board-"

TWITCH_PURPLE = 0x9146FF
YOUTUBE_RED = 0xFF0033
TIKTOK_CYAN = 0x25F4EE

TWITCH_URL = "https://twitch.tv/GreMi_Gaming"
TIKTOK_URL = "https://www.tiktok.com/@ttv.gremi_gaming"

# Custom emojis on the GreMi_Gaming server (buttons, and the logos in the picture)
EMOJI = {
    "twitch": {"id": "1085833562474938438", "name": "twitch"},
    "youtube": {"id": "1508733155883094066", "name": "Youtube_logo"},
    "tiktok": {"id": "1097447464010788864", "name": "TikTok"},
}


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
        print(f"socials board: {getattr(fn, '__name__', 'source')} unavailable ({e})")
        return None


def fetch_bytes(url):
    """Downloads a picture, or None. Never fails the run."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 race-control"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(5_000_000)
    except Exception as e:
        print(f"socials board: picture unavailable ({url.split('?')[0]}: {e})")
        return None


# ---------- latest stream and videos ----------
def twitch_details(stats):
    """Profile picture, offline banner and the latest past broadcast from Twitch."""
    headers = stats.twitch_headers()
    if headers is None:
        return None
    users = stats.http("GET", "https://api.twitch.tv/helix/users?login="
                       + urllib.parse.quote(stats.TWITCH_LOGIN), headers).get("data", [])
    if not users:
        return None
    user = users[0]
    details = {"banner": user.get("offline_image_url") or None, "video": None}
    videos = stats.http("GET", "https://api.twitch.tv/helix/videos?first=1&type=archive&user_id="
                        + user["id"], headers).get("data", [])
    if videos:
        v = videos[0]
        thumb = (v.get("thumbnail_url") or "").replace("%{width}", "320") \
            .replace("%{height}", "180")
        # Twitch shows a placeholder picture while a broadcast is still processing
        details["video"] = {"id": v.get("id", ""), "title": v.get("title", ""),
                            "url": v.get("url", ""),
                            "thumb": thumb if "_404/" not in thumb else ""}
    return details


def youtube_latest(stats):
    """The newest upload on the YouTube channel."""
    if not (stats.YOUTUBE_API_KEY and stats.YOUTUBE_CHANNEL_ID):
        return None
    key = "&key=" + urllib.parse.quote(stats.YOUTUBE_API_KEY)
    items = stats.http("GET", "https://www.googleapis.com/youtube/v3/channels"
                       "?part=contentDetails&id="
                       + urllib.parse.quote(stats.YOUTUBE_CHANNEL_ID) + key).get("items", [])
    uploads = (items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
               if items else None)
    if not uploads:
        return None
    latest = stats.http("GET", "https://www.googleapis.com/youtube/v3/playlistItems"
                        "?part=snippet&maxResults=1&playlistId="
                        + urllib.parse.quote(uploads) + key).get("items", [])
    if not latest:
        return None
    sn = latest[0]["snippet"]
    vid = sn.get("resourceId", {}).get("videoId", "")
    t = sn.get("thumbnails", {})
    thumb = next((t[k]["url"] for k in ("medium", "high", "default") if k in t), "")
    return {"id": vid, "title": sn.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={vid}", "thumb": thumb}


def tiktok_latest(stats):
    """The newest TikTok. Needs the video.list permission in the TikTok login."""
    token = getattr(stats, "_tiktok_access", {}).get("token")
    if not token:
        return None
    result = stats.http("POST", stats.TIKTOK_API
                        + "/video/list/?fields=id,title,video_description,cover_image_url,share_url",
                        {"Authorization": f"Bearer {token}"}, {"max_count": 1})
    error = result.get("error", {})
    if error.get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok video list: {error.get('code')}")
    videos = result.get("data", {}).get("videos", [])
    if not videos:
        return None
    v = videos[0]
    return {"id": v.get("id", ""), "title": v.get("title") or v.get("video_description") or "",
            "url": v.get("share_url") or TIKTOK_URL, "thumb": v.get("cover_image_url", "")}


# ---------- the board ----------
def live_badge(stream):
    if not stream:
        return "OFFLINE"
    viewers = stream.get("viewer_count")
    return f"LIVE \u00b7 {viewers:,}" if viewers is not None else "LIVE"


def gather(stats):
    """Everything the board shows, as plain data (no pictures yet)."""
    stream = safe(stats.twitch_stream)
    twitch = safe(lambda: twitch_details(stats)) or {}
    if stream:
        label = "Live now"
        thumb = (stream.get("thumbnail_url", "").replace("{width}", "320")
                 .replace("{height}", "180"))
        twitch_preview = {"label": label, "title": stream.get("title", ""), "thumb": thumb,
                          "id": stream.get("started_at", ""), "url": TWITCH_URL}
    elif twitch.get("video"):
        v = twitch["video"]
        twitch_preview = {"label": "Last stream", "title": v["title"], "id": v["id"],
                          "thumb": v.get("thumb") or twitch.get("banner") or "", "url": v["url"]}
    else:
        twitch_preview = None
    yt = safe(lambda: youtube_latest(stats))
    tt = safe(lambda: tiktok_latest(stats))
    return [
        {"key": "twitch", "name": "Twitch", "color": TWITCH_PURPLE,
         "count": safe(stats.twitch_followers), "word": "followers",
         "badge": live_badge(stream), "live": bool(stream),
         "preview": twitch_preview},
        {"key": "youtube", "name": "YouTube", "color": YOUTUBE_RED,
         "count": safe(stats.youtube_subscribers), "word": "subscribers",
         "preview": dict(yt, label="Latest video") if yt else None},
        {"key": "tiktok", "name": "TikTok", "color": TIKTOK_CYAN,
         "count": safe(stats.tiktok_followers), "word": "followers",
         "preview": dict(tt, label="Latest TikTok") if tt else None},
    ]


def fingerprint(columns):
    """Changes when anything visible on the picture changes. Uses video ids, not
    picture addresses, because TikTok's picture addresses change every day."""
    keep = [(c["count"], c.get("badge"),
             c["preview"] and (c["preview"]["label"], c["preview"]["title"], c["preview"]["id"]))
            for c in columns]
    import board_image
    return hashlib.sha1(json.dumps([board_image.LAYOUT, keep]).encode()).hexdigest()[:12]


def components_for(columns):
    """Link buttons: the platforms, then the latest stream and videos."""
    live = columns[0]["live"]
    links = [("Watch live" if live else "Twitch", TWITCH_URL, "twitch"),
             ("YouTube", youtube_url(), "youtube"),
             ("TikTok", TIKTOK_URL, "tiktok")]
    previews = [(c["preview"]["label"], c["preview"].get("url"), c["key"])
                for c in columns if c["preview"] and not (c["key"] == "twitch" and live)]
    rows = [{"type": 1, "components": [
        {"type": 2, "style": 5, "label": label, "url": url, "emoji": EMOJI[key]}
        for label, url, key in row if url]} for row in (links, previews)]
    return [r for r in rows if r["components"]]


def build(stats, columns=None):
    """(message, picture bytes, file name) for the board."""
    import board_image
    columns = columns or gather(stats)
    name = f"{FILE_PREFIX}{fingerprint(columns)}.png"
    for c in columns:
        e = EMOJI[c["key"]]
        c["logo"] = fetch_bytes(f"https://cdn.discordapp.com/emojis/{e['id']}.png?size=96")
        if c["preview"]:
            c["preview"]["image"] = fetch_bytes(c["preview"].get("thumb"))
    message = {"content": HEADER, "embeds": [], "components": components_for(columns),
               "attachments": [{"id": 0, "filename": name}],
               "allowed_mentions": {"parse": []}}
    return message, board_image.render(columns), name


def buttons(message):
    return [(b.get("label"), b.get("url")) for row in message.get("components", [])
            for b in row.get("components", [])]


def send(stats, method, path, message, picture, name):
    """Sends a message with the picture attached (Discord needs multipart for files)."""
    boundary = "----raceControl" + hashlib.sha1(picture).hexdigest()[:16]
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{json.dumps(message)}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            f"filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + picture + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Authorization": f"Bot {stats.DISCORD_TOKEN}", "User-Agent": stats.UA,
               "Content-Type": f"multipart/form-data; boundary={boundary}"}
    for _ in range(4):
        req = urllib.request.Request(stats.DISCORD + path, data=body, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.2)
                continue
            raise RuntimeError(f"{method} {path} failed: {e.code} {e.read()[:200]!r}")
    raise RuntimeError(f"{method} {path} failed after retries")


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
    columns = gather(stats)
    current = [a.get("filename") for a in board.get("attachments", [])]
    name = f"{FILE_PREFIX}{fingerprint(columns)}.png"
    if (current == [name] and not board.get("embeds")
            and buttons(board) == buttons({"components": components_for(columns)})):
        print("unchanged: socials board")
        return
    message, picture, name = build(stats, columns)
    send(stats, "PATCH", f"/channels/{SOCIALS_CHANNEL_ID}/messages/{board['id']}",
         message, picture, name)
    print("updated:   socials board")


def main(argv):
    import update_stats
    message, picture, name = build(update_stats)
    if "--post" in argv:
        if not SOCIALS_CHANNEL_ID:
            raise SystemExit("Set SOCIALS_CHANNEL_ID first")
        if find_board(update_stats):
            raise SystemExit("The board is already posted")
        msg = send(update_stats, "POST", f"/channels/{SOCIALS_CHANNEL_ID}/messages",
                   message, picture, name)
        print(f"posted socials board {msg['id']}")
    else:
        out = argv[argv.index("--preview") + 1] if "--preview" in argv[:-1] else name
        with open(out, "wb") as f:
            f.write(picture)
        print(json.dumps(message, indent=2, ensure_ascii=False))
        print(f"picture written to {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
