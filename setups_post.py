"""GreMi's car setups in the F1-SETUPS forum, one Race Control post per track.

The race stats site (gremigaming/race-stats) renders the pictures on every
build: discord/setups/<id>.png, listed per track in discord/setups.json with
a hash that only changes when the picture does. Per track there's an overview
(dry race, dry quali and wet side by side, compact) and a full card per kind,
or a grayed "No setup yet" placeholder.

The post's opening message holds the overview, so that's the forum thumbnail;
the full cards follow as one message each. Messages are edited when a picture
changes, so posts don't jump around. New posts are made last track first: the
forum sorts newest on top, so the posts end up in calendar order.
"""
import hashlib
import json
import os

import leaderboard_post

SETUPS_FORUM_ID = os.environ.get("SETUPS_FORUM_ID", "")
SITE = leaderboard_post.SITE
COMPONENTS_V2 = 1 << 15
CONTAINER, TEXT, GALLERY, SEPARATOR, ROW, BUTTON = 17, 10, 12, 14, 1, 2

RED, VIOLET, SKY, GRAY = 0xE10600, 0x8B5CF6, 0x0EA5E9, 0x3F3F46
KINDS = {  # kind: (title, emoji, colour)
    "race": ("Dry race", "☀️", RED),
    "dry": ("Dry", "☀️", RED),
    "quali": ("Dry quali", "⏱️", VIOLET),
    "wet": ("Wet", "🌧️", SKY),
}
FLAGS = {
    "Australia": "🇦🇺", "China": "🇨🇳", "Japan": "🇯🇵", "Bahrain": "🇧🇭", "Saudi Arabia": "🇸🇦",
    "Miami": "🇺🇸", "Emilia-Romagna": "🇮🇹", "Canada": "🇨🇦", "Monaco": "🇲🇨", "Barcelona": "🇪🇸",
    "Austria": "🇦🇹", "Great Britain": "🇬🇧", "Belgium": "🇧🇪", "Hungary": "🇭🇺",
    "Netherlands": "🇳🇱", "Monza": "🇮🇹", "Madrid": "🇪🇸", "Azerbaijan": "🇦🇿",
    "Singapore": "🇸🇬", "Austin": "🇺🇸", "Mexico": "🇲🇽", "Brazil": "🇧🇷", "Las Vegas": "🇺🇸",
    "Qatar": "🇶🇦", "Abu Dhabi": "🇦🇪",
}
# Forum tag per post when the name isn't a tag itself
TAGS = {"Barcelona": "Spain", "Madrid": "Spain", "Monza": "Italy", "Emilia-Romagna": "Italy",
        "Miami": "United States", "Austin": "United States", "Las Vegas": "United States"}


def file_name(card, text=""):
    digest = hashlib.sha256((card["hash"] + text).encode()).hexdigest()[:12]
    return f"{card['picture'][:-4]}-{digest}.png"


def text(content):
    return {"type": TEXT, "content": content}


def short_date(card):
    """"2 Oct 2026" out of "Used in the race on 2 Oct 2026"."""
    return (card.get("date") or "").rsplit(" on ", 1)[-1]


def opening(track, overview, cards):
    """The post's opening message: the overview picture and what's in the post."""
    lines = []
    real = {c["kind"]: c for c in cards if not c.get("placeholder")}
    dry = real.get("race") or real.get("quali") or real.get("dry")
    for kind in ("race", "quali", "wet"):
        title, emoji, _ = KINDS[kind]
        card = real.get(kind) or (dry if kind == "race" else None)
        if card:
            lines.append(f"{emoji} **{title}** ─ 📅 {short_date(card) or 'saved'}")
        elif kind == "quali" and dry:
            lines.append(f"{emoji} **{title}** ─ 🔁 same as the dry race setup")
        else:
            lines.append(f"{emoji} **{title}** ─ ⏳ no setup yet")
    summary = "\n".join(lines)
    head = (f"# {FLAGS.get(track['post'], '🏁')} {track['post']}\n"
            f"📍 **{overview.get('circuit') or track['post']}** · 🏎️ GreMi_Gaming's setups")
    name = file_name(overview, head + summary)
    return name, {
        "flags": COMPONENTS_V2,
        "components": [
            {"type": CONTAINER, "accent_color": RED, "components": [
                text(head),
                {"type": GALLERY, "items": [{"media": {"url": f"attachment://{name}"}}]},
                {"type": SEPARATOR, "divider": True, "spacing": 2},
                text(summary),
                {"type": SEPARATOR, "divider": True, "spacing": 1},
                text("-# 🔄 Updates by itself after every race · 👇 full setups below"),
            ]},
            {"type": ROW, "components": [
                {"type": BUTTON, "style": 5, "label": "Race stats", "emoji": {"name": "📊"},
                 "url": SITE}]},
        ],
        "attachments": [{"id": 0, "filename": name}],
        "allowed_mentions": {"parse": []},
    }


def card_message(card):
    """One full setup card, in its own coloured box."""
    title, emoji, colour = KINDS.get(card["kind"], (card["kind"], "🏁", GRAY))
    if card.get("placeholder"):
        note = ("shows up here after GreMi's next wet race on this track" if card["kind"] == "wet"
                else "shows up here after GreMi's next race on this track")
        head = f"## {emoji} {title} setup\n-# ⏳ No setup yet · {note}"
        colour = GRAY
    else:
        date = card.get("date") or ""
        head = (f"## {emoji} {card.get('label') or title} setup\n"
                f"-# 🏁 {date}{' · ' if date else ''}🏎️ GreMi_Gaming")
    name = file_name(card, head)
    return name, {
        "flags": COMPONENTS_V2,
        "components": [{"type": CONTAINER, "accent_color": colour, "components": [
            text(head),
            {"type": GALLERY, "items": [{"media": {"url": f"attachment://{name}"}}]},
        ]}],
        "attachments": [{"id": 0, "filename": name}],
        "allowed_mentions": {"parse": []},
    }


def forum_posts(stats, me):
    """Race Control's own posts in the forum by lower-case name, open and archived."""
    posts = {}
    found = [t for t in stats.discord("GET", f"/guilds/{stats.GUILD_ID}/threads/active")
             .get("threads", []) if t.get("parent_id") == SETUPS_FORUM_ID]
    before = ""
    while True:
        page = stats.discord("GET", f"/channels/{SETUPS_FORUM_ID}/threads/archived/public"
                                    f"?limit=100{before}")
        found += page.get("threads", [])
        if not page.get("has_more") or not page.get("threads"):
            break
        before = "&before=" + page["threads"][-1]["thread_metadata"]["archive_timestamp"]
    for t in found:
        if t.get("owner_id") == me:
            posts.setdefault(t["name"].strip().lower(), t)
    return posts


def picture(stats, card):
    return leaderboard_post.fetch(stats, f"discord/setups/{card['picture']}?{card['hash']}")


def picture_name(message):
    """The file name of a message's picture: in its gallery for our boxed
    messages (Discord swaps attachment:// for its own address), else attached."""
    for c in message.get("components") or []:
        for inner in c.get("components") or []:
            for item in inner.get("items") or []:
                url = (item.get("media") or {}).get("url", "")
                if url:
                    return url.split("?")[0].rsplit("/", 1)[-1]
    return next((a.get("filename", "") for a in message.get("attachments", [])), "")


def kind_of(message):
    """The card kind a message of ours holds, from its picture's file name."""
    name = picture_name(message)
    for kind in ("overview", "race", "quali", "wet", "dry"):
        if f"-{kind}-" in name:
            return kind
    return None


def tag_ids(stats):
    forum = stats.discord("GET", f"/channels/{SETUPS_FORUM_ID}")
    return {t["name"].lower(): t["id"] for t in forum.get("available_tags", [])}


def sync_track(stats, me, track, post, tags):
    import socials_board
    overview = next(c for c in track["cards"] if c["kind"] == "overview")
    cards = [c for c in track["cards"] if c["kind"] != "overview"]
    name, message = opening(track, overview, cards)
    if not post:
        tag = tags.get(TAGS.get(track["post"], track["post"]).lower())
        thread = socials_board.send(stats, "POST", f"/channels/{SETUPS_FORUM_ID}/threads", {
            "name": track["post"], "applied_tags": [tag] if tag else [], "message": message,
        }, [(name, picture(stats, overview))])
        for card in cards:
            n, m = card_message(card)
            socials_board.send(stats, "POST", f"/channels/{thread['id']}/messages", m,
                               [(n, picture(stats, card))])
        print(f"posted:    {track['post']} setups")
        return
    msgs = [m for m in stats.discord("GET", f"/channels/{post['id']}/messages?limit=50")
            if m["author"]["id"] == me]
    by_kind = {kind_of(m): m for m in reversed(msgs)}
    wanted = [("overview", name, message, overview)] + [
        (c["kind"], *card_message(c), c) for c in cards]
    todo = [w for w in wanted
            if not by_kind.get(w[0]) or picture_name(by_kind[w[0]]) != w[1]]
    gone = [m for k, m in by_kind.items() if k not in {w[0] for w in wanted} and m["id"] != post["id"]]
    if not todo and not gone:
        print(f"unchanged: {track['post']} setups")
        return
    archived = (post.get("thread_metadata") or {}).get("archived")
    if archived:
        # an archived post takes no edits; reopening it doesn't move it
        stats.discord("PATCH", f"/channels/{post['id']}", {"archived": False})
    for kind, n, m, card in todo:
        old = by_kind.get(kind) or (by_kind.get("race") if kind == "dry" else None) \
            or (by_kind.get("dry") if kind == "race" else None)
        if kind == "overview":
            old = old or {"id": post["id"]}
        if old:
            socials_board.send(stats, "PATCH", f"/channels/{post['id']}/messages/{old['id']}",
                               m, [(n, picture(stats, card))])
        else:
            socials_board.send(stats, "POST", f"/channels/{post['id']}/messages", m,
                               [(n, picture(stats, card))])
    for m in gone:
        if not (kind_of(m) in ("race", "dry") and {"race", "dry"} & {w[0] for w in wanted}):
            stats.discord("DELETE", f"/channels/{post['id']}/messages/{m['id']}")
    print(f"updated:   {track['post']} setups")
    if archived:
        stats.discord("PATCH", f"/channels/{post['id']}", {"archived": True})


def update(stats):
    if not SETUPS_FORUM_ID:
        return
    manifest = json.loads(leaderboard_post.fetch(stats, f"discord/setups.json?{os.urandom(4).hex()}"))
    me = stats.discord("GET", "/users/@me")["id"]
    posts = forum_posts(stats, me)
    tags = tag_ids(stats)
    # last track first: the forum shows the newest post on top
    for track in reversed(manifest["tracks"]):
        post = posts.get(track["post"].strip().lower())
        if post and (post.get("thread_metadata") or {}).get("locked"):
            print(f"skipped:   {track['post']} post is locked")
            continue
        sync_track(stats, me, track, post, tags)
