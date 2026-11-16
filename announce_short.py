"""Schedule a Race Control post in NEW-CONTENT for a new Short (GreMi 2026-11-16).

Adds one entry to bot/scheduled_posts.json; the bot restarts on that commit and
posts it at the given Amsterdam time. Shorts are lighter than highlight posts:
no Notify Me ping, short text, only the YouTube link unfurls (TikTok is wrapped
in <> so Discord shows no second preview).

    python3 announce_short.py --title "P12 TO THE WIN | F1 26" \
        --youtube https://www.youtube.com/watch?v=ID --at "2026-11-16 18:05" \
        [--tiktok URL] [--note "From Sunday's stream highlight 👇"] [--highlight URL]

Used by the manual "Announce Short" workflow, so a clip thread can schedule it.
Re-running with the same YouTube link replaces that Short's post (unless it went out).
"""
import argparse
import datetime
import json
import pathlib
import re

CONFIG = pathlib.Path(__file__).parent / "bot" / "scheduled_posts.json"
NEW_CONTENT = 1080505995467431986
TIKTOK_PROFILE = "https://www.tiktok.com/@ttv.gremi_gaming"
YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})")


def youtube_id(url):
    m = YT_ID.search(url)
    if not m:
        raise SystemExit(f"not a YouTube video link: {url}")
    return m.group(1)


def short_text(title, youtube, tiktok=None, note=None, highlight=None):
    lines = ["# 📱 New Short!", "", f"**{title.strip()}**", ""]
    if note:
        lines += [note.strip(), ""]
    lines.append(f"▶️ YouTube: https://www.youtube.com/shorts/{youtube_id(youtube)}")
    lines.append(f"🎵 TikTok: <{tiktok or TIKTOK_PROFILE}>")
    if highlight:
        lines.append(f"🎬 Full highlight: <{highlight}>")
    return "\n".join(lines)


def short_post(title, youtube, at, tiktok=None, note=None, highlight=None):
    datetime.datetime.strptime(at, "%Y-%m-%d %H:%M")  # same format the bot reads
    return {"key": f"short-{youtube_id(youtube)}", "channel": NEW_CONTENT, "post_at": at,
            "text": short_text(title, youtube, tiktok, note, highlight)}


def add(config, post):
    """Replace a post with the same key, otherwise append. Returns the new config."""
    posts = [p for p in config["posts"] if p["key"] != post["key"]]
    posts.append(post)
    return {**config, "posts": posts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--youtube", required=True)
    ap.add_argument("--at", required=True, help="Amsterdam time, YYYY-MM-DD HH:MM")
    ap.add_argument("--tiktok")
    ap.add_argument("--note")
    ap.add_argument("--highlight")
    a = ap.parse_args()
    post = short_post(a.title, a.youtube, a.at, a.tiktok or None, a.note or None,
                      a.highlight or None)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    CONFIG.write_text(json.dumps(add(config, post), indent=1, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(f"{post['key']} at {post['post_at']}\n---\n{post['text']}")


if __name__ == "__main__":
    main()
