"""Log of GreMi's Twitch streams (streams.json), for the race stats site.

GreMi 2026-11-10: the site's streams only show races driven during a stream.
Every stats run (update_stats.py, every 5 minutes) asks Twitch whether the
channel is live and which past broadcasts it keeps, and keeps one window per
stream here:

    {"since": "...", "streams": [{"start": "2026-11-10T18:02:11Z", "end": "...Z" | null}]}

"end" is null while live. A stream that ends gets its end from its past
broadcast (start + duration), or the time of the first offline run when
Twitch keeps no past broadcasts. Only changes are written, so a stream costs
two commits: one at the start, one at the end. race-stats
scripts/build-league-data.mjs reads this file and leaves races outside every
window (from "since" on) off the site.
"""

import datetime as dt
import json
import os
import re
import urllib.parse

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "streams.json")
SAME = 15 * 60   # a past broadcast starting this close to a logged start is the same stream (s)


def parse(t):
    return dt.datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def duration(text):
    """Twitch's "3h2m1s" -> seconds."""
    return sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in re.findall(r"(\d+)([hms])", text or ""))


def merge(log, live_start, vods, now):
    """New log from the old one, the live stream's start (or None when offline),
    the past broadcasts [(start, end)] and the current time (all datetimes)."""
    streams = [{"start": parse(s["start"]), "end": parse(s["end"]) if s.get("end") else None}
               for s in log.get("streams", [])]

    def find(start):
        return next((s for s in streams if abs((s["start"] - start).total_seconds()) <= SAME), None)

    for start, end in vods:
        s = find(start)
        if s is None:
            streams.append({"start": start, "end": end})
        elif s["end"] is not None or live_start is None or find(live_start) is not s:
            s["end"] = end          # the broadcast's own end is the exact one
    if live_start is not None:
        s = find(live_start)
        if s is None:
            streams.append({"start": live_start, "end": None})
        else:
            s["start"], s["end"] = min(s["start"], live_start), None
    for s in streams:
        if s["end"] is None and (live_start is None or find(live_start) is not s):
            s["end"] = now          # went offline and Twitch kept no broadcast
    streams.sort(key=lambda s: s["start"])
    out = {"about": "GreMi's Twitch streams (stream_log.py); races outside these windows are not stream races",
           "since": log.get("since") or (iso(streams[0]["start"]) if streams else None),
           "streams": [{"start": iso(s["start"]), "end": iso(s["end"]) if s["end"] else None} for s in streams]}
    return out


def update(m):
    """m = update_stats (shares its Twitch login and answers)."""
    headers = m.twitch_headers()
    if headers is None:
        print("skipped:   stream log (Twitch not configured)")
        return
    live = m.twitch_is_live()
    stream = m.twitch_stream()
    live_start = parse(stream["started_at"]) if live and stream and stream.get("started_at") else None
    users = m.http("GET", "https://api.twitch.tv/helix/users?login=" + urllib.parse.quote(m.TWITCH_LOGIN),
                   headers).get("data", [])
    vods = []
    if users:
        videos = m.http("GET", "https://api.twitch.tv/helix/videos?type=archive&first=20&user_id="
                        + users[0]["id"], headers).get("data", [])
        for v in videos:
            start = parse(v["created_at"])
            vods.append((start, start + dt.timedelta(seconds=duration(v.get("duration")))))
    log = json.load(open(PATH)) if os.path.exists(PATH) else {}
    new = merge(log, live_start, vods, dt.datetime.now(dt.timezone.utc).replace(microsecond=0))
    if new == log or (not new["streams"] and not log):
        print("unchanged: stream log")
        return
    with open(PATH, "w") as f:
        json.dump(new, f, indent=1)
        f.write("\n")
    print(f"updated:   stream log ({len(new['streams'])} streams)")
