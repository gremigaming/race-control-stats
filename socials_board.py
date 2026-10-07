"""The socials board in the Socials channel.

Three Race Control messages, one per platform (Twitch, YouTube, TikTok): a card
in the platform colour with its logo, the follower number and the latest stream
or video linked, and the logo top right. All three link buttons sit together
under the last card. The stats workflow edits those same
messages every run, and only when something on them changed.

The workflow never posts a new message by itself. Posting is done once by hand
(after the owner approved it):
    python3 socials_board.py --preview             prints the messages, sends nothing
    python3 socials_board.py --post                posts the missing platform messages
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
OLD_HEADERS = (f"## \U0001F3C1 GreMi_Gaming · {TITLE}", f"## GreMi_Gaming\n-# {TITLE}")
PLATFORMS = ("Twitch", "YouTube", "TikTok")
TAGLINE = {"twitch": "Live sim racing", "youtube": "Races and highlights",
           "tiktok": "Clips and highlights"}
FILE_PREFIX = "socials-board-"

TWITCH_PURPLE = 0x9146FF
YOUTUBE_RED = 0xFF0033
TIKTOK_CYAN = 0x25F4EE  # the colour strip in the single-card backup
TIKTOK_DARK = 0x161823  # TikTok's own dark blue-black, the card's accent
BOARD_GREY = 0x3F4147  # the side line; the platform colours are in the strip

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
         "badge": live_badge(stream), "live": bool(stream), "stream": stream,
         "preview": twitch_preview},
        {"key": "youtube", "name": "YouTube", "color": YOUTUBE_RED,
         "count": safe(stats.youtube_subscribers), "word": "subscribers",
         "preview": dict(yt, label="Latest video") if yt else None},
        {"key": "tiktok", "name": "TikTok", "color": TIKTOK_DARK,
         "count": safe(stats.tiktok_followers), "word": "followers",
         "preview": dict(tt, label="Latest TikTok") if tt else None},
    ]


def fingerprint(columns):
    """Changes when anything visible on the picture changes. Uses video ids, not
    picture addresses, because TikTok's picture addresses change every day."""
    keep = [(c["count"], c.get("badge"), (c.get("stream") or {}).get("game_name"),
             c["preview"] and (c["preview"]["label"], c["preview"]["title"], c["preview"]["id"]))
            for c in columns]
    import board_image
    return hashlib.sha1(json.dumps([board_image.LAYOUT, keep]).encode()).hexdigest()[:12]


BUTTON_LABEL = {"twitch": "Follow on Twitch", "youtube": "Subscribe on YouTube",
                "tiktok": "Follow on TikTok"}


def link_button(col):
    """The link button at the bottom of a platform's card."""
    key = col["key"]
    label = "Watch live" if col.get("live") else BUTTON_LABEL[key]
    return {"type": 2, "style": 5, "label": label, "url": platform_url(key),
            "emoji": EMOJI[key]}


def button_for(col):
    """A button row (used by the single-card backup, where buttons sit below)."""
    if not platform_url(col["key"]):
        return []
    return [{"type": 1, "components": [dict(link_button(col), label={
        "twitch": "Watch live" if col.get("live") else "Twitch",
        "youtube": "YouTube", "tiktok": "TikTok"}[col["key"]])]}]


def sans_bold(text):
    """Math Sans Bold letters and digits (the look GreMi picked), e.g. \U0001D5DA\U0001D5FF\U0001D5F2\U0001D5E0\U0001D5F6."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D5D4 + ord(ch) - ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D5EE + ord(ch) - ord("a")))
        elif "0" <= ch <= "9":
            out.append(chr(0x1D7EC + ord(ch) - ord("0")))
        else:
            out.append(ch)
    return "".join(out)


# The header the board used to have; it went so all three cards fit on one screen
HEADER = f"## {sans_bold('GreMi_Gaming')}\n-# {sans_bold(TITLE)}"

# Titles are cut to one line so every card has the same height
TITLE_LIMIT = 36


def link(title, url, limit=TITLE_LIMIT):
    import board_image
    text = short(board_image.clean(title), limit) or "Watch"
    text = sans_bold(text.replace("[", "(").replace("]", ")"))
    return f"[{text}]({url})" if url else text


def short(text, limit):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


def bold_caps(text):
    """The bold capitals the server uses in channel names, like \U0001D5E7\U0001D5EA\U0001D5DC."""
    out = []
    for ch in text.upper():
        out.append(chr(0x1D5D4 + ord(ch) - ord("A")) if "A" <= ch <= "Z" else ch)
    return "".join(out)


def platform_url(key):
    return {"twitch": TWITCH_URL, "youtube": youtube_url(), "tiktok": TIKTOK_URL}[key]


def card_text(col):
    """A platform card, always three lines so the cards match: the number as a
    heading, the latest stream or video as one link, and a small grey line. (An
    invisible width line wrapped into a big gap on phones; the picture under the
    text already makes every card full width.)"""
    count = f"{col['count']:,}" if col.get("count") is not None else "\u2014"
    lines = [f"## {count} {col['word']}"]
    preview, stream = col["preview"], col.get("stream")
    if stream:
        viewers = stream.get("viewer_count")
        meta = "Live now \u00b7 " + (stream.get("game_name") or "Sim racing")
        if viewers is not None:
            meta += f" \u00b7 {viewers:,} watching"
        lines += [f"\U0001F534 {link(stream.get('title', ''), TWITCH_URL, TITLE_LIMIT - 3)}",
                  f"-# {sans_bold(meta)}"]
    elif preview:
        meta = preview["label"] + (" \u00b7 offline right now" if col["key"] == "twitch" else "")
        lines += [link(preview["title"], preview["url"]), f"-# {sans_bold(meta)}"]
    else:
        url = platform_url(col["key"])
        follow = sans_bold(f"Follow on {col['name']}")
        lines += [f"[{follow}]({url})" if url else follow,
                  f"-# {sans_bold(TAGLINE[col['key']])}"]
    return "\n".join(lines)


# Cut shorter in the side-by-side columns (a third of the card) so a title
# never wraps onto a second line
FIELD_TITLE_LIMIT = 14


def field_for(col):
    """One platform column: logo and name on top, the number, a blank line for
    air, then what the latest stream or video is and its link. (Headings and the
    small grey -# text don't work inside columns, Discord shows them as is.)"""
    e = EMOJI[col["key"]]
    count = f"{col['count']:,}" if col.get("count") is not None else "\u2014"
    preview, stream = col["preview"], col.get("stream")
    if stream:
        viewers = stream.get("viewer_count")
        label = "\U0001F534 Live now" + (f" \u00b7 {viewers:,}" if viewers is not None else "")
        target = link(stream.get("title", ""), TWITCH_URL, FIELD_TITLE_LIMIT)
    elif preview:
        label = preview["label"]
        target = link(preview["title"], preview["url"], FIELD_TITLE_LIMIT)
    else:
        label = TAGLINE[col["key"]]
        url = platform_url(col["key"])
        follow = sans_bold("Follow")
        target = f"[{follow}]({url})" if url else follow
    value = f"**{count}** {col['word']}\n\n{label}\n{target}"
    return {"name": f"<:{e['name']}:{e['id']}> {col['name']}", "value": value,
            "inline": True}


def logo_url(key):
    return f"https://cdn.discordapp.com/emojis/{EMOJI[key]['id']}.png?size=128"


def embed_for(col, thumb_name):
    # the logo sits top right as the card's thumbnail, which also widens the card
    embed = {"color": col["color"],
             "author": {"name": col["name"]},
             "description": card_text(col),
             "thumbnail": {"url": logo_url(col["key"])}}
    if platform_url(col["key"]):
        embed["author"]["url"] = platform_url(col["key"])
    if thumb_name:
        # under the text rather than beside it, which squeezes the text on phones
        embed["image"] = {"url": f"attachment://{thumb_name}"}
    return embed


# Discord's newer message layout: a card (container) holding the text with the
# logo beside it
COMPONENTS_V2 = 1 << 15
ROW, CONTAINER, SECTION, TEXT, THUMBNAIL, GALLERY = 1, 17, 9, 10, 11, 12
# a bit shorter than in the old cards, since the button takes room on the right
CARD_TITLE_LIMIT = 30


def card_v2_text(col):
    """Logo and name, the number as a heading, the latest stream or video as one
    link, and a small grey line."""
    count = f"{col['count']:,}" if col.get("count") is not None else "\u2014"
    lines = [f"### {col['name']}", f"## {count} {col['word']}"]
    preview, stream = col["preview"], col.get("stream")
    if stream:
        viewers = stream.get("viewer_count")
        meta = "Live now \u00b7 " + (stream.get("game_name") or "Sim racing")
        if viewers is not None:
            meta += f" \u00b7 {viewers:,} watching"
        lines += [f"\U0001F534 {link(stream.get('title', ''), TWITCH_URL, CARD_TITLE_LIMIT - 3)}",
                  f"-# {sans_bold(meta)}"]
    elif preview:
        meta = preview["label"] + (" \u00b7 offline right now" if col["key"] == "twitch" else "")
        lines += [link(preview["title"], preview["url"], CARD_TITLE_LIMIT),
                  f"-# {sans_bold(meta)}"]
    else:
        lines.append(f"-# {sans_bold(TAGLINE[col['key']])}")
    return "\n".join(lines)


def spacer_name(key):
    import board_image
    return f"{FILE_PREFIX}{key}-spacer-{board_image.LAYOUT}.png"


def message_for(col, rows=()):
    """A platform's own message and its picture: a card in the platform colour
    with the text and the logo top right, plus an invisible full-width picture
    that makes every card the same width (the widest Discord allows) while
    adding almost no height. `rows` go under the card, outside it."""
    import board_image
    key = col["key"]
    name = spacer_name(key)
    parts = [{"type": SECTION,
              "components": [{"type": TEXT, "content": card_v2_text(col)}],
              "accessory": {"type": THUMBNAIL, "media": {"url": logo_url(key)}}},
             {"type": GALLERY, "items": [{"media": {"url": f"attachment://{name}"}}]}]
    message = {"flags": COMPONENTS_V2, "content": "", "embeds": [],
               "attachments": [{"id": 0, "filename": name}],
               "components": [{"type": CONTAINER, "accent_color": col["color"],
                               "components": parts}] + list(rows),
               "allowed_mentions": {"parse": []}}
    return message, [(name, board_image.spacer())]


def buttons_row(columns):
    """All three link buttons together, under the last card."""
    return {"type": ROW, "components": [link_button(c) for c in columns
                                        if platform_url(c["key"])]}


def build(stats, columns=None):
    """{platform key: (message, [(file name, picture bytes)])} for the three board
    messages (Twitch, YouTube, TikTok); the last one carries all the buttons."""
    columns = columns or gather(stats)
    last = columns[-1]["key"]
    return {c["key"]: message_for(c, [buttons_row(columns)] if c["key"] == last else ())
            for c in columns}


def single_card(stats, columns=None):
    """The one-card layout GreMi kept as a backup (2026-10-07): the three platforms
    side by side in one embed with a glowing colour strip below.
    Returns (message, [(file name, picture bytes)])."""
    import board_image
    columns = columns or gather(stats)
    fp = fingerprint(columns)
    name = f"{FILE_PREFIX}strip-{fp}.png"
    strip_colors = [TIKTOK_CYAN if c["key"] == "tiktok" else c["color"] for c in columns]
    files = [(name, board_image.strip(strip_colors))]
    embeds = [{"color": BOARD_GREY, "fields": [field_for(c) for c in columns],
               "image": {"url": f"attachment://{name}"}}]
    rows = [{"type": 1, "components": [b for c in columns for row in button_for(c)
                                       for b in row["components"]]}]
    message = {"content": "", "embeds": embeds, "components": rows,
               "attachments": [{"id": i, "filename": n} for i, (n, _) in enumerate(files)],
               "allowed_mentions": {"parse": []}}
    return message, files


def buttons(message):
    return [(b.get("label"), b.get("url")) for row in message.get("components", [])
            for b in row.get("components", [])]


def send(stats, method, path, message, files):
    """Sends a message with pictures attached (Discord needs multipart for files)."""
    boundary = "----raceControl" + hashlib.sha1(b"".join(f[1] for f in files)).hexdigest()[:16]
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{json.dumps(message)}\r\n").encode()
    for i, (name, data) in enumerate(files):
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[{i}]\"; "
                 f"filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() \
            + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
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


def _parts(components):
    """The texts, colours and buttons in a message's components, in order."""
    out = []
    for c in components or []:
        media = [(c.get("media") or {}).get("url", "")] + [
            (i.get("media") or {}).get("url", "") for i in c.get("items", [])]
        # pictures by file name: Discord swaps attachment:// for its own address
        media = [m.split("?")[0].rsplit("/", 1)[-1] for m in media if m]
        out.append((c.get("type"), c.get("content"), c.get("accent_color"),
                    c.get("label"), c.get("url"), media))
        out += _parts(c.get("components"))
        if c.get("accessory"):
            out += _parts([c["accessory"]])
    return out


def visible(message):
    """What a reader sees of a board message, to tell if it needs an edit."""
    cards = [(e.get("color"), (e.get("author") or {}).get("name"), e.get("description"),
              (e.get("thumbnail") or {}).get("url"), bool(e.get("image")), bool(e.get("fields")))
             for e in message.get("embeds", [])]
    return (cards, message.get("content") or "", _parts(message.get("components")),
            bool((message.get("flags") or 0) & COMPONENTS_V2))


KEYS = {"Twitch": "twitch", "YouTube": "youtube", "TikTok": "tiktok"}


def platform_of(msg):
    """Which platform a board message is for, or None if it isn't one."""
    embeds = msg.get("embeds") or [{}]
    name = (embeds[0].get("author") or {}).get("name")
    fields = [f.get("name", "").split()[-1] for f in embeds[0].get("fields", [])]
    if msg.get("embeds") and len(embeds) == 1 and name in KEYS:
        return KEYS[name]
    texts = " ".join(p[1] or "" for p in _parts(msg.get("components")) if p[0] == TEXT)
    for name, key in KEYS.items():  # cards start with the platform name
        e = EMOJI[key]
        if texts.startswith((f"### {name}", f"**{name}**", f"<:{e['name']}:{e['id']}>")):
            return key
    # the older one-message boards count as the Twitch message: it came first
    if (msg.get("content") in (HEADER,) + OLD_HEADERS or embeds[0].get("title") == TITLE
            or [(e.get("author") or {}).get("name") for e in embeds] == list(PLATFORMS)
            or fields == list(PLATFORMS)):
        return "twitch"
    return None


def find_boards(stats):
    """{platform key: message} for the board messages already in the Socials channel."""
    me = stats.discord("GET", "/users/@me")["id"]
    found = {}
    for msg in stats.discord("GET", f"/channels/{SOCIALS_CHANNEL_ID}/messages?limit=50"):
        key = platform_of(msg) if msg["author"]["id"] == me else None
        if key:
            found.setdefault(key, msg)  # newest first, so the newest wins
    return found


def update(stats):
    """Edits each platform's message when something on it changed. Called by
    update_stats.main(). Never posts: a missing message is skipped."""
    if not SOCIALS_CHANNEL_ID:
        return
    boards = find_boards(stats)
    if not boards:
        print("socials board: not posted yet, skipping")
        return
    for key, (message, files) in build(stats).items():
        board = boards.get(key)
        if board is None:
            print(f"socials board: no {key} message posted yet, skipping")
        elif visible(board) == visible(message):
            print(f"unchanged: socials board ({key})")
        else:
            send(stats, "PATCH", f"/channels/{SOCIALS_CHANNEL_ID}/messages/{board['id']}",
                 message, files)
            print(f"updated:   socials board ({key})")


def main(argv):
    import update_stats
    messages = build(update_stats)
    if "--post" in argv:
        # posts only the platforms that have no message yet, in board order
        if not SOCIALS_CHANNEL_ID:
            raise SystemExit("Set SOCIALS_CHANNEL_ID first")
        existing = find_boards(update_stats)
        for key, (message, files) in messages.items():
            if key not in existing:
                msg = send(update_stats, "POST", f"/channels/{SOCIALS_CHANNEL_ID}/messages",
                           message, files)
                print(f"posted socials board ({key}) {msg['id']}")
    else:
        for key, (message, files) in messages.items():
            for name, data in files:
                with open(name, "wb") as f:
                    f.write(data)
                print(f"picture written to {name}")
            print(json.dumps(message, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
