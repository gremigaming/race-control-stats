"""Keeps the server in step with GreMi's Twitch schedule.

1. Discord events: one event per upcoming stream in the next 7 days. New streams
   get an event, changed ones are edited, removed or cancelled ones are deleted.
   Events Signupbot already made are taken over, not doubled.
2. The schedule picture in the Stream Schedule channel: one post per week
   (Monday to Sunday), edited whenever the schedule changes. A new week's post
   pings Notify Me; the very first post and all edits are silent.

Called by update_stats.main() every run.
"""
import datetime
import hashlib
import json
import os
import urllib.parse
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("SCHEDULE_TIMEZONE", "Europe/Amsterdam"))
SCHEDULE_CHANNEL_ID = os.environ.get("SCHEDULE_CHANNEL_ID", "")
NOTIFY_ROLE_ID = os.environ.get("NOTIFY_ROLE_ID", "")
CHANNEL_NAME = "GreMi_Gaming"
FILE_PREFIX = "stream-schedule-"
HEADER = "**\U0001F4C5  STREAM SCHEDULE  ·  {week}**"
DEFAULT_LENGTH = datetime.timedelta(hours=2)
WINDOW = datetime.timedelta(days=7)
EXTERNAL = 3  # Discord event type for "somewhere else" (a link)
SCHEDULED = 1


def parse_time(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def iso(t):
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def twitch_url(login):
    return f"https://twitch.tv/{login.lower()}"


# ---------------------------------------------------------------- Twitch

def fetch_schedule(stats, since):
    """(segments, vacation) from Twitch, starting at `since`. Each segment has
    id, start, end, title, category, cancelled. vacation is (start, end) or None."""
    headers = stats.twitch_headers()
    if headers is None:
        return None, None
    users = stats.http("GET", "https://api.twitch.tv/helix/users?login="
                       + urllib.parse.quote(stats.TWITCH_LOGIN), headers).get("data", [])
    if not users:
        raise RuntimeError("Twitch user not found, check TWITCH_LOGIN")
    url = ("https://api.twitch.tv/helix/schedule?first=25&broadcaster_id=" + users[0]["id"]
           + "&start_time=" + urllib.parse.quote(iso(since)))
    try:
        data = stats.http("GET", url, headers).get("data") or {}
    except RuntimeError as e:
        if " 404 " in str(e):  # no schedule at all on Twitch
            return [], None
        if " 400 " not in str(e):
            raise
        # Twitch didn't take the start of the week: ask from now instead
        data = stats.http("GET", url.split("&start_time=")[0], headers).get("data") or {}
    segments = []
    for seg in data.get("segments") or []:
        start = parse_time(seg["start_time"])
        end = parse_time(seg["end_time"]) if seg.get("end_time") else start + DEFAULT_LENGTH
        segments.append({"id": seg["id"], "start": start, "end": end,
                         "title": (seg.get("title") or "").strip() or "Live on Twitch",
                         "category": (seg.get("category") or {}).get("name") or "",
                         "cancelled": bool(seg.get("canceled_until"))})
    vac = data.get("vacation")
    vacation = (parse_time(vac["start_time"]), parse_time(vac["end_time"])) if vac else None
    return segments, vacation


# ---------------------------------------------------------------- events

def event_body(seg, login):
    desc = f"Live on Twitch: {twitch_url(login)}"
    if seg["category"]:
        desc = f"{seg['category']}\n{desc}"
    return {"name": seg["title"][:100], "description": desc[:1000],
            "scheduled_start_time": iso(seg["start"]), "scheduled_end_time": iso(seg["end"])}


def plan_events(events, segments, login, me, now):
    """What to change so there is exactly one event per upcoming stream.

    Only touches events that link to the Twitch channel and haven't started.
    Returns [("create", seg), ("edit", event, seg, changes), ("delete", event)].
    """
    url = twitch_url(login)
    ours = [e for e in events
            if e.get("entity_type") == EXTERNAL and e.get("status") == SCHEDULED
            and ((e.get("entity_metadata") or {}).get("location") or "").lower().rstrip("/") == url
            and parse_time(e["scheduled_start_time"]) > now]
    # Signupbot's events first (it would make a new one if its own disappeared),
    # then the oldest
    ours.sort(key=lambda e: (e.get("creator_id") == me, int(e["id"])))
    wanted = sorted((s for s in segments if not s["cancelled"]
                     and now < s["start"] <= now + WINDOW), key=lambda s: s["start"])

    pairs, free = [], list(ours)
    rest = []
    for seg in wanted:  # same start time: the same stream
        match = next((e for e in free if parse_time(e["scheduled_start_time"]) == seg["start"]), None)
        if match:
            free.remove(match)
            pairs.append((match, seg))
        else:
            rest.append(seg)
    actions = []
    for seg in rest:  # same title at a new time: the stream moved
        match = next((e for e in free if e.get("name") == seg["title"][:100]), None)
        if match:
            free.remove(match)
            pairs.append((match, seg))
        else:
            actions.append(("create", seg))
    for event, seg in pairs:
        body = event_body(seg, login)
        changes = {}
        for k, v in body.items():
            have = event.get(k) or ""
            if k.startswith("scheduled_"):
                same = have and parse_time(have) == parse_time(v)
            else:
                same = have == v
            if not same:
                changes[k] = v
        if changes:
            actions.append(("edit", event, seg, changes))
    for event in free:
        if parse_time(event["scheduled_start_time"]) <= now + WINDOW:
            actions.append(("delete", event))
    return actions


def sync_events(stats, segments, now):
    login = stats.TWITCH_LOGIN
    me = stats.discord("GET", "/users/@me")["id"]
    events = stats.discord("GET", f"/guilds/{stats.GUILD_ID}/scheduled-events")
    actions = plan_events(events, segments, login, me, now)
    for action in actions:
        if action[0] == "create":
            seg = action[1]
            body = event_body(seg, login)
            body.update({"privacy_level": 2, "entity_type": EXTERNAL,
                         "entity_metadata": {"location": twitch_url(login)}})
            made = stats.discord("POST", f"/guilds/{stats.GUILD_ID}/scheduled-events", body)
            print(f"event created: {seg['title']} at {iso(seg['start'])} ({made.get('id')})")
        elif action[0] == "edit":
            event, seg, changes = action[1:]
            stats.discord("PATCH", f"/guilds/{stats.GUILD_ID}/scheduled-events/{event['id']}",
                          changes)
            print(f"event updated: {seg['title']} ({', '.join(changes)})")
        else:
            event = action[1]
            stats.discord("DELETE", f"/guilds/{stats.GUILD_ID}/scheduled-events/{event['id']}")
            print(f"event deleted: {event.get('name')} (no longer on Twitch)")
    if not actions:
        print("unchanged: stream events")


# ---------------------------------------------------------------- picture

def week_start(now):
    local = now.astimezone(TZ)
    monday = local.date() - datetime.timedelta(days=local.weekday())
    return monday, datetime.datetime.combine(monday, datetime.time(), TZ)


def week_label(monday):
    sunday = monday + datetime.timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day} - {sunday.day} {sunday.strftime('%b')}"
    return f"{monday.day} {monday.strftime('%b')} - {sunday.day} {sunday.strftime('%b')}"


def tz_label(when):
    offset = when.astimezone(TZ).utcoffset()
    hours = int(offset.total_seconds() // 3600)
    return f"GMT{hours:+d}" if hours else "GMT"


def week_days(monday, segments, vacation):
    """Seven (date, [stream]) pairs for the picture, Monday first."""
    days = []
    for i in range(7):
        day = monday + datetime.timedelta(days=i)
        streams = [{"time": s["start"].astimezone(TZ).strftime("%H:%M"), "title": s["title"],
                    "category": s["category"], "cancelled": s["cancelled"]}
                   for s in sorted(segments, key=lambda s: s["start"])
                   if s["start"].astimezone(TZ).date() == day]
        if vacation and not streams:
            noon = datetime.datetime.combine(day, datetime.time(12), TZ)
            if vacation[0] <= noon <= vacation[1]:
                streams = [{"vacation": True}]
        days.append((day, streams))
    return days


def fingerprint(days, today):
    import schedule_image
    data = [schedule_image.LAYOUT, today.isoformat(),
            [(d.isoformat(), s) for d, s in days]]
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]


def find_posts(stats):
    """Race Control's schedule posts in the channel, newest first."""
    me = stats.discord("GET", "/users/@me")["id"]
    msgs = stats.discord("GET", f"/channels/{SCHEDULE_CHANNEL_ID}/messages?limit=50")
    return [m for m in msgs if m["author"]["id"] == me
            and any(a.get("filename", "").startswith(FILE_PREFIX)
                    for a in m.get("attachments", []))]


def update_picture(stats, segments, vacation, now):
    import schedule_image
    import socials_board
    monday, _ = week_start(now)
    today = now.astimezone(TZ).date()
    week = week_label(monday)
    days = week_days(monday, segments, vacation)
    name = f"{FILE_PREFIX}{monday.isoformat()}-{fingerprint(days, today)}.png"
    posts = find_posts(stats)
    this_week = next((m for m in posts if any(
        a["filename"].startswith(f"{FILE_PREFIX}{monday.isoformat()}-")
        for a in m.get("attachments", []))), None)
    if this_week and any(a["filename"] == name for a in this_week["attachments"]):
        print("unchanged: stream schedule picture")
        return
    picture = schedule_image.render(days, today, tz_label(now), CHANNEL_NAME, week)
    content = HEADER.format(week=week.upper())
    content += f"\n-# Times in {tz_label(now)} · updates automatically from Twitch"
    message = {"content": content, "attachments": [{"id": 0, "filename": name}],
               "allowed_mentions": {"parse": []}}
    if this_week:
        message["content"] = this_week["content"]  # keep the ping line as it was
        socials_board.send(stats, "PATCH",
                           f"/channels/{SCHEDULE_CHANNEL_ID}/messages/{this_week['id']}",
                           message, [(name, picture)])
        print("updated:   stream schedule picture")
        return
    if posts and NOTIFY_ROLE_ID:  # a new week, and not the very first post
        message["content"] += f"\n<@&{NOTIFY_ROLE_ID}>"
        message["allowed_mentions"] = {"roles": [NOTIFY_ROLE_ID]}
    msg = socials_board.send(stats, "POST", f"/channels/{SCHEDULE_CHANNEL_ID}/messages",
                             message, [(name, picture)])
    print(f"posted:    stream schedule picture {msg.get('id')}")


# ---------------------------------------------------------------- entry

def update(stats, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    _, since = week_start(now)
    segments, vacation = fetch_schedule(stats, since)
    if segments is None:
        print("stream schedule: Twitch isn't configured, skipping")
        return
    sync_events(stats, segments, now)
    if not SCHEDULE_CHANNEL_ID:
        return
    try:
        import schedule_image  # noqa: F401  (needs Pillow)
    except ImportError:
        print("stream schedule picture: Pillow missing, skipping")
        return
    # the week's streams only (Twitch also sends next week's)
    monday, start = week_start(now)
    end = start + datetime.timedelta(days=7)
    update_picture(stats, [s for s in segments if start <= s["start"] < end], vacation, now)
