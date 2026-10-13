"""Sends a finished clip from the private clip-studio repo to GreMi's TikTok drafts.

Run by the "Post clip" workflow with the clip's folder name, e.g.
    python3 post_clip.py singapore-ferrari
It reads clips-ready/<clip>/video.mp4 and post.md from clip-studio, uploads the
video to the TikTok inbox (drafts), DMs GreMi the caption on Discord (one message
he can copy straight into TikTok) and puts post.md in the run summary. Nothing
goes public: GreMi opens TikTok, adds a sound and the caption, and posts.

    python3 post_clip.py stats
lists GreMi's latest TikTok videos with views, likes, comments and shares.
"""
import re
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIKTOK_API = "https://open.tiktokapis.com/v2"
CLIPS_REPO = "gremigaming/clip-studio"
GH_TOKEN = os.environ.get("GH_TOKEN", "")
CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("TIKTOK_REFRESH_TOKEN", "")
# Where to write TikTok's new refresh token when it changes, so the workflow can save it
REFRESH_TOKEN_OUT = os.environ.get("TIKTOK_REFRESH_TOKEN_OUT", "")
SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY", "")
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_API = "https://discord.com/api/v10"
GUILD_ID = "1079917337165172876"


def http(method, url, headers=None, data=None, form=False):
    """JSON request to TikTok; errors show TikTok's own message, never the token."""
    headers = dict(headers or {})
    if form:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json; charset=UTF-8"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except ValueError:
            raise RuntimeError(f"{method} {url} failed: {e.code} {raw[:200]!r}")


def clip_file(clip, name):
    """One file of a finished clip, straight from the private clip-studio repo."""
    url = f"https://api.github.com/repos/{CLIPS_REPO}/contents/clips-ready/{clip}/{name}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.raw",
        "Authorization": f"Bearer {GH_TOKEN}",
        "User-Agent": "race-control-clips",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Could not read clips-ready/{clip}/{name} from clip-studio ({e.code})")


def tiktok_access():
    token = http("POST", TIKTOK_API + "/oauth/token/", data={
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }, form=True)
    if "access_token" not in token:
        raise RuntimeError(f"TikTok login expired or invalid ({token.get('error', 'unknown error')}),"
                           " run the TikTok login workflow again")
    new_refresh = token.get("refresh_token")
    if new_refresh and new_refresh != REFRESH_TOKEN and REFRESH_TOKEN_OUT:
        with open(REFRESH_TOKEN_OUT, "w", encoding="utf-8") as f:
            f.write(new_refresh)
    if "video.upload" not in token.get("scope", ""):
        raise RuntimeError("The TikTok login has no video.upload permission, run the TikTok login workflow again")
    return token["access_token"]


def check(resp, what):
    error = resp.get("error") or {}
    if error.get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok {what} failed: {error.get('code')} {error.get('message', '')}")
    return resp.get("data") or {}


def tiktok_draft(video):
    """Uploads the video to the TikTok inbox and waits until it has arrived."""
    auth = {"Authorization": f"Bearer {tiktok_access()}"}
    size = len(video)
    init = check(http("POST", TIKTOK_API + "/post/publish/inbox/video/init/", auth, {
        "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                        "chunk_size": size, "total_chunk_count": 1},
    }), "upload start")
    req = urllib.request.Request(init["upload_url"], data=video, method="PUT", headers={
        "Content-Type": "video/mp4",
        "Content-Length": str(size),
        "Content-Range": f"bytes 0-{size - 1}/{size}",
    })
    with urllib.request.urlopen(req, timeout=300) as resp:
        resp.read()
    state = ""
    for _ in range(120):
        time.sleep(5)
        status = check(http("POST", TIKTOK_API + "/post/publish/status/fetch/", auth,
                            {"publish_id": init["publish_id"]}), "status check")
        if status.get("status", "") != state:
            state = status.get("status", "")
            print(f"TikTok status: {state}", flush=True)
        if state in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return state
        if state == "FAILED":
            raise RuntimeError(f"TikTok could not process the clip ({status.get('fail_reason', 'no reason given')})")
    raise RuntimeError(f"TikTok was still on {state} after 10 minutes; check the TikTok inbox before running again")


def tiktok_videos(count=10):
    """GreMi's latest TikTok videos with their numbers (needs the video.list scope)."""
    auth = {"Authorization": f"Bearer {tiktok_access()}"}
    fields = "id,title,create_time,duration,view_count,like_count,comment_count,share_count,share_url"
    data = check(http("POST", f"{TIKTOK_API}/video/list/?fields={fields}", auth,
                      {"max_count": count}), "video list")
    return data.get("videos") or []


def show_stats():
    rows = ["| Posted (UTC) | Views | Likes | Comments | Shares | Video |", "|---|---|---|---|---|---|"]
    for v in tiktok_videos():
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(v.get("create_time", 0)))
        title = (v.get("title") or "").replace("|", "/").replace("\n", " ")[:60]
        rows.append(f"| {when} | {v.get('view_count', 0)} | {v.get('like_count', 0)} | "
                    f"{v.get('comment_count', 0)} | {v.get('share_count', 0)} | "
                    f"[{title or 'video'}]({v.get('share_url', '')}) |")
    table = "\n".join(rows)
    print(table)
    if SUMMARY:
        with open(SUMMARY, "a", encoding="utf-8") as f:
            f.write("## Latest TikTok videos\n\n" + table + "\n")


def caption_from(post):
    """TikTok caption + hashtags from post.md, ready to paste."""
    caption = re.search(r"## TikTok.*?\*\*Caption:\*\*\n(.+?)\n\n", post, re.S)
    tags = re.search(r"## TikTok.*?\*\*Hashtags:\*\* (.+?)\n", post, re.S)
    if not caption:
        return ""
    return caption.group(1).strip() + ("\n\n" + tags.group(1).strip() if tags else "")


def discord(method, path, data=None):
    req = urllib.request.Request(DISCORD_API + path, method=method,
                                 data=json.dumps(data).encode() if data is not None else None, headers={
        "Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json",
        "User-Agent": "DiscordBot (race-control, 1)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def dm_caption(clip, caption):
    """DMs the server owner (GreMi): a heads-up, then the caption on its own to copy."""
    if not (DISCORD_TOKEN and caption):
        return False
    try:
        owner = discord("GET", f"/guilds/{GUILD_ID}")["owner_id"]
        channel = discord("POST", "/users/@me/channels", {"recipient_id": owner})["id"]
        discord("POST", f"/channels/{channel}/messages", {
            "content": f"🎬 **{clip}** is in your TikTok drafts. Hold the next message, "
                       "tap Copy Text and paste it as the caption. Then add a sound at 0% and post."})
        discord("POST", f"/channels/{channel}/messages", {"content": caption})
        return True
    except (urllib.error.URLError, KeyError) as e:
        print(f"Could not send the caption on Discord ({getattr(e, 'code', e)})")
        return False


def main(argv):
    if len(argv) < 2 or not argv[1].strip():
        raise SystemExit("Give the clip's folder name, e.g. singapore-ferrari")
    clip = argv[1].strip().strip("/")
    if "/" in clip or clip.startswith("."):
        raise SystemExit("The clip name is just the folder name inside clips-ready")
    for name, value in (("TIKTOK_CLIENT_KEY", CLIENT_KEY),
                        ("TIKTOK_CLIENT_SECRET", CLIENT_SECRET), ("TIKTOK_REFRESH_TOKEN", REFRESH_TOKEN)):
        if not value:
            raise SystemExit(f"{name} is missing")
    if clip == "stats":
        return show_stats()
    if not GH_TOKEN:
        raise SystemExit("GH_SECRETS_TOKEN is missing")
    video = clip_file(clip, "video.mp4")
    post = clip_file(clip, "post.md").decode("utf-8")
    print(f"Sending {clip} ({len(video) / 1e6:.1f} MB) to TikTok drafts", flush=True)
    tiktok_draft(video)
    sent = dm_caption(clip, caption_from(post))
    print("Done: open TikTok, tap the inbox notification, add a sound and the caption, and post."
          + (" The caption is in your Discord DMs." if sent else ""))
    if SUMMARY:
        with open(SUMMARY, "a", encoding="utf-8") as f:
            f.write(f"## {clip} is in your TikTok drafts\n\nOpen TikTok, tap the notification, "
                    "add a sound and paste the caption.\n\n" + post + "\n")


if __name__ == "__main__":
    main(sys.argv)
