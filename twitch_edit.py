"""Edits GreMi's Twitch schedule and runs ads (run by the "Twitch schedule" workflow).

Needs a user login as the channel owner with the schedule and ads permissions
(SCOPE), made once:
  login-start   asks Twitch for a code; GreMi enters it at twitch.tv/activate
  login-finish  waits for that approval and writes the refresh token to
                TWITCH_REFRESH_TOKEN_OUT, which the workflow saves as the
                TWITCH_REFRESH_TOKEN secret
After that:
  run '<json>'  one change, for example
    {"action": "list"}
    {"action": "add", "start": "2026-10-12 20:00", "duration": 120,
     "title": "F1 league night", "category": "F1 25", "recurring": false}
    {"action": "edit", "id": "<segment id>", "start": "...", "title": "..."}
    {"action": "cancel", "id": "<segment id>"}   (or "uncancel")
    {"action": "delete", "id": "<segment id>"}
    {"action": "vacation", "start": "2026-10-20 00:00", "end": "2026-10-27 00:00"}
    {"action": "vacation-off"}
    {"action": "ad", "length": 90}   runs an ad now (only while live, 30-180 s)
    {"action": "ads"}                when the next scheduled ad is due
    {"action": "snooze"}             pushes the next scheduled ad back 5 minutes
  Times are Europe/Amsterdam unless "timezone" says otherwise.
Never prints tokens.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
LOGIN = os.environ.get("TWITCH_LOGIN", "")
REFRESH_TOKEN = os.environ.get("TWITCH_REFRESH_TOKEN", "")
OUT = os.environ.get("TWITCH_REFRESH_TOKEN_OUT", "")
GITHUB_OUTPUT = os.environ.get("GITHUB_OUTPUT", "")
SCOPE = os.environ.get("TWITCH_SCOPES") or \
    "channel:manage:schedule channel:edit:commercials channel:read:ads channel:manage:ads"
HELIX = "https://api.twitch.tv/helix"
DEFAULT_TZ = "Europe/Amsterdam"


def request(method, url, data=None, headers=None, form=False):
    """(status, json body). Never raises on an HTTP error status."""
    headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw[:200].decode(errors="replace")}


def save_refresh(token):
    if token and token != REFRESH_TOKEN and OUT:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(token)


# ---------------------------------------------------------------- login

def login_start():
    status, res = request("POST", "https://id.twitch.tv/oauth2/device",
                          {"client_id": CLIENT_ID, "scopes": SCOPE}, form=True)
    if status != 200:
        sys.exit(f"Twitch refused the login request: {status} {res.get('message', '')}")
    print(f"Open {res['verification_uri']} as {LOGIN or 'the channel owner'} "
          f"and enter the code {res['user_code']}")
    print(f"The code works for {res['expires_in'] // 60} minutes.")
    if GITHUB_OUTPUT:
        with open(GITHUB_OUTPUT, "a", encoding="utf-8") as f:
            f.write(f"device_code={res['device_code']}\n")
            f.write(f"interval={res.get('interval', 5)}\n")
            f.write(f"expires_in={res['expires_in']}\n")


def login_finish(device_code, interval=5, expires_in=1800):
    deadline = time.time() + int(expires_in)
    while time.time() < deadline:
        time.sleep(int(interval))
        status, res = request("POST", "https://id.twitch.tv/oauth2/token", {
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "scopes": SCOPE,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }, form=True)
        if status == 200:
            save_refresh(res["refresh_token"])
            check = user_headers(res["access_token"])
            print(f"Approved by {whoami(check)['display_name']}. Saved the login.")
            return
        message = str(res.get("message", ""))
        if "authorization_pending" in message:
            continue
        if "slow_down" in message:
            interval = int(interval) + 5
            continue
        sys.exit(f"Login failed: {status} {message}")
    sys.exit("The code expired before it was approved. Run the workflow again.")


# ---------------------------------------------------------------- schedule

def user_headers(access_token):
    return {"Client-Id": CLIENT_ID, "Authorization": f"Bearer {access_token}"}


def whoami(headers):
    status, res = request("GET", HELIX + "/users", headers=headers)
    if status != 200 or not res.get("data"):
        sys.exit(f"Could not read the Twitch account: {status} {res.get('message', '')}")
    return res["data"][0]


def access():
    if not REFRESH_TOKEN:
        sys.exit("No Twitch login yet: run the workflow with 'login' first.")
    status, res = request("POST", "https://id.twitch.tv/oauth2/token", {
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN,
    }, form=True)
    if status != 200:
        sys.exit(f"Twitch login expired or was removed ({res.get('message', status)}), "
                 "run the workflow with 'login' again.")
    save_refresh(res.get("refresh_token"))
    return user_headers(res["access_token"])


def to_utc(text, tz):
    """'2026-10-12 20:00' in tz (or a full ISO time) -> RFC3339 UTC."""
    t = dt.datetime.fromisoformat(text.strip().replace(" ", "T").replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=ZoneInfo(tz))
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def category_id(headers, name):
    status, res = request("GET", HELIX + "/search/categories?first=5&query="
                          + urllib.parse.quote(name), headers=headers)
    found = res.get("data", []) if status == 200 else []
    exact = [c for c in found if c["name"].lower() == name.lower()]
    if not (exact or found):
        sys.exit(f"No Twitch category called {name!r}")
    pick = (exact or found)[0]
    print(f"Category: {pick['name']}")
    return pick["id"]


def show(segments, tz):
    zone = ZoneInfo(tz)
    for s in segments:
        start = dt.datetime.fromisoformat(s["start_time"].replace("Z", "+00:00")).astimezone(zone)
        end = dt.datetime.fromisoformat(s["end_time"].replace("Z", "+00:00")).astimezone(zone) \
            if s.get("end_time") else None
        flags = (" [cancelled]" if s.get("canceled_until") else "") + \
                (" [weekly]" if s.get("is_recurring") else "")
        cat = (s.get("category") or {}).get("name", "")
        print(f"{start:%a %d %b %H:%M}" + (f"-{end:%H:%M}" if end else "")
              + f"  {s.get('title') or '(no title)'}  {cat}{flags}  id={s['id']}")


def run(cmd):
    tz = cmd.get("timezone", DEFAULT_TZ)
    headers = access()
    me = whoami(headers)
    bid = me["id"]
    action = cmd.get("action", "list")
    base = f"{HELIX}/schedule/segment?broadcaster_id={bid}"

    if action == "list":
        status, res = request("GET", f"{HELIX}/schedule?first=25&broadcaster_id={bid}", headers=headers)
        if status == 404:
            print("The schedule is empty.")
            return
        data = res.get("data") or {}
        if data.get("vacation"):
            print(f"Vacation: {data['vacation']['start_time']} to {data['vacation']['end_time']}")
        show(data.get("segments") or [], tz)
        return

    if action in ("ad", "ads", "snooze"):
        ads(action, cmd, bid, headers, tz)
        return

    if action in ("add", "edit"):
        body = {}
        if "start" in cmd:
            body["start_time"] = to_utc(cmd["start"], tz)
            body["timezone"] = tz
        if "duration" in cmd:
            body["duration"] = str(int(cmd["duration"]))
        if "title" in cmd:
            body["title"] = cmd["title"][:140]
        if cmd.get("category"):
            body["category_id"] = category_id(headers, cmd["category"])
        if action == "add":
            body.setdefault("timezone", tz)
            body["is_recurring"] = bool(cmd.get("recurring", False))
            status, res = request("POST", base, body, headers)
        else:
            status, res = request("PATCH", f"{base}&id={urllib.parse.quote(cmd['id'])}", body, headers)
    elif action in ("cancel", "uncancel"):
        status, res = request("PATCH", f"{base}&id={urllib.parse.quote(cmd['id'])}",
                              {"is_canceled": action == "cancel"}, headers)
    elif action == "delete":
        status, res = request("DELETE", f"{base}&id={urllib.parse.quote(cmd['id'])}", headers=headers)
    elif action in ("vacation", "vacation-off"):
        q = {"broadcaster_id": bid, "is_vacation_enabled": str(action == "vacation").lower()}
        if action == "vacation":
            q.update(vacation_start_time=to_utc(cmd["start"], tz),
                     vacation_end_time=to_utc(cmd["end"], tz), timezone=tz)
        status, res = request("PATCH", f"{HELIX}/schedule/settings?" + urllib.parse.urlencode(q),
                              headers=headers)
    else:
        sys.exit(f"Unknown action {action!r}")

    if status not in (200, 204):
        sys.exit(f"Twitch said no: {status} {res.get('message', '')}")
    print(f"Done: {action}")
    segments = (res.get("data") or {}).get("segments") if isinstance(res.get("data"), dict) else None
    if segments:
        show(segments, tz)


def ads(action, cmd, bid, headers, tz):
    if action == "ad":
        length = min(180, max(30, int(cmd.get("length", 90))))
        status, res = request("POST", HELIX + "/channels/commercial",
                              {"broadcaster_id": bid, "length": length}, headers)
    elif action == "snooze":
        status, res = request("POST", f"{HELIX}/channels/ads/schedule/snooze?broadcaster_id={bid}",
                              headers=headers)
    else:
        status, res = request("GET", f"{HELIX}/channels/ads?broadcaster_id={bid}", headers=headers)
    if status in (401, 403):
        sys.exit("The Twitch login has no ads permission yet: run the workflow with 'login' "
                 "and approve it as the channel owner.")
    if status != 200:
        sys.exit(f"Twitch said no: {status} {res.get('message', '')}")
    data = (res.get("data") or [{}])[0]
    zone = ZoneInfo(tz)

    def when(value):
        if not value:
            return "none"
        if isinstance(value, (int, float)) or str(value).isdigit():
            t = dt.datetime.fromtimestamp(int(value), dt.timezone.utc)
        else:
            t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return f"{t.astimezone(zone):%a %d %b %H:%M:%S}"

    if action == "ad":
        print(f"Ad running: {data.get('length')} seconds. {data.get('message', '')}".strip())
        print(f"Next ad possible in {data.get('retry_after')} seconds.")
    elif action == "snooze":
        print(f"Snoozed. Next ad: {when(data.get('next_ad_at'))}, "
              f"snoozes left: {data.get('snooze_count')}")
    else:
        print(f"Next ad: {when(data.get('next_ad_at'))} ({data.get('duration')} s), "
              f"last ad: {when(data.get('last_ad_at'))}, "
              f"pre-roll free for {data.get('preroll_free_time')} s, "
              f"snoozes left: {data.get('snooze_count')} (more at {when(data.get('snooze_refresh_at'))})")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "login-start":
        login_start()
    elif what == "login-finish":
        login_finish(*sys.argv[2:5])
    elif what == "run":
        run(json.loads(sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].strip() else "{}"))
    else:
        sys.exit(__doc__)
