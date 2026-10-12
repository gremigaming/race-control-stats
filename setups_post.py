"""Posts GreMi's car setup per track in the F1-SETUPS forum.

The race stats site (gremigaming/race-stats) renders a picture per setup card
on every build: discord/setups/<card>.png, listed per forum post (track) in
discord/setups.json with a hash that only changes when the card does. A track
has a dry card (race and quali when they differ), a wet card when there is
one, or a grayed "No setup yet" placeholder.

Race Control keeps one message per forum post, edited when a card changes, so
the posts don't jump around. Tracks whose post doesn't exist yet are skipped.
"""
import json
import os

import leaderboard_post

SETUPS_FORUM_ID = os.environ.get("SETUPS_FORUM_ID", "")
PREFIX = "setup-"
KINDS = {"race": "dry race", "quali": "dry quali", "wet": "wet", "dry": "dry"}


def forum_posts(stats):
    """Every post in the forum by lower-case name, open and archived."""
    posts = {}
    active = stats.discord("GET", f"/guilds/{stats.GUILD_ID}/threads/active")
    for t in active.get("threads", []):
        if t.get("parent_id") == SETUPS_FORUM_ID:
            posts.setdefault(t["name"].strip().lower(), t)
    before = ""
    while True:
        page = stats.discord("GET", f"/channels/{SETUPS_FORUM_ID}/threads/archived/public"
                                    f"?limit=100{before}")
        for t in page.get("threads", []):
            posts.setdefault(t["name"].strip().lower(), t)
        if not page.get("has_more") or not page.get("threads"):
            return posts
        before = "&before=" + page["threads"][-1]["thread_metadata"]["archive_timestamp"]


def message_for(track):
    cards = track["cards"]
    if all(c.get("placeholder") for c in cards):
        note = "No setup for this track yet, it shows up here after GreMi's next race on it"
    else:
        kinds = [KINDS.get(c["kind"], c["kind"]) for c in cards if not c.get("placeholder")]
        note = f"{', '.join(kinds).capitalize()} · updates by itself when the setup changes"
    return {
        "content": f"## 🛠️ GreMi's {track['post']} setup\n-# {note}",
        "allowed_mentions": {"parse": []},
    }


def update(stats):
    if not SETUPS_FORUM_ID:
        return
    import socials_board
    manifest = json.loads(leaderboard_post.fetch(stats, f"discord/setups.json?{os.urandom(4).hex()}"))
    me = stats.discord("GET", "/users/@me")["id"]
    posts = forum_posts(stats)
    for track in manifest["tracks"]:
        post = posts.get(track["post"].strip().lower())
        if not post:
            print(f"skipped:   no forum post for {track['post']}")
            continue
        if (post.get("thread_metadata") or {}).get("locked"):
            print(f"skipped:   {track['post']} post is locked")
            continue
        names = [c["picture"][:-4] + f"-{c['hash']}.png" for c in track["cards"]]
        msgs = stats.discord("GET", f"/channels/{post['id']}/messages?limit=50")
        mine = next((m for m in msgs if m["author"]["id"] == me and any(
            a.get("filename", "").startswith(PREFIX) for a in m.get("attachments", []))), None)
        if mine and sorted(a["filename"] for a in mine["attachments"]) == sorted(names):
            print(f"unchanged: {track['post']} setup")
            continue
        pictures = [(n, leaderboard_post.fetch(stats, f"discord/setups/{c['picture']}?{c['hash']}"))
                    for n, c in zip(names, track["cards"])]
        message = message_for(track)
        message["attachments"] = [{"id": i, "filename": n} for i, n in enumerate(names)]
        archived = (post.get("thread_metadata") or {}).get("archived")
        if archived:
            # an archived post takes no edits; reopening it doesn't move it
            stats.discord("PATCH", f"/channels/{post['id']}", {"archived": False})
        if mine:
            socials_board.send(stats, "PATCH", f"/channels/{post['id']}/messages/{mine['id']}",
                               message, pictures)
            print(f"updated:   {track['post']} setup")
        else:
            socials_board.send(stats, "POST", f"/channels/{post['id']}/messages", message, pictures)
            print(f"posted:    {track['post']} setup")
        if archived:
            stats.discord("PATCH", f"/channels/{post['id']}", {"archived": True})
