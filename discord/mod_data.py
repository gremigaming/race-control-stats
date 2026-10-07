"""Collects what the weekly mod report needs, straight from the Discord API.

Run by the "Race Control: mod report" routine (a Claude Code session), so the
report costs no Claude API use. It reads the last DAYS days of messages and the
audit log, and writes a digest for Claude to read:

  python3 discord/mod_data.py collect /tmp/mod-digest.md
  python3 discord/mod_data.py post /tmp/mod-report.md   # posts in MOD-TRACKER

The digest holds member messages: keep it in /tmp, never commit it.
In the routine the proxy adds the bot token; elsewhere set DISCORD_BOT_TOKEN.
"""
import collections
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
GUILD_ID = "1079917337165172876"
MOD_TRACKER_ID = "1557356701794050148"
STAFF_ROLES = {"1080524470332174437": "Moderator", "1085850009490178100": "Lead Moderator"}
DAYS = 90
MOD_CHARS = 12000       # newest message text kept per mod
CANDIDATES = 15         # most active non-staff members looked at
CANDIDATE_CHARS = 2500  # per candidate
LIMIT = 1900            # Discord allows 2000 characters per message
EPOCH = 1420070400000   # Discord's snowflake epoch, in ms

# Audit log action types that are mod actions
ACTIONS = {20: "kick", 22: "ban", 23: "unban", 24: "member_update", 25: "role_update",
           26: "voice_move", 27: "voice_disconnect", 72: "message_delete",
           73: "bulk_delete"}


def call(method, path, body=None):
    headers = {"User-Agent": "race-control (mod report, 1)", "Content-Type": "application/json"}
    if os.environ.get("DISCORD_BOT_TOKEN"):
        headers["Authorization"] = "Bot " + os.environ["DISCORD_BOT_TOKEN"]
    for _ in range(6):
        req = urllib.request.Request(API + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read()
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 1)) + 0.3)
                continue
            if e.code in (403, 404):  # a channel the bot can't read
                return None
            raise
    raise RuntimeError(f"rate limited on {path}")


def snowflake(ms):
    return str((int(ms) - EPOCH) << 22)


def stamp(iso):
    return iso[:10]


def describe(entry, names):
    """One audit log entry as a short line, or None when it isn't a mod action."""
    kind = ACTIONS.get(entry.get("action_type"))
    if not kind:
        return None
    who = names.get(entry.get("target_id"), "someone")
    reason = entry.get("reason")
    why = f" (reason: {reason[:80]})" if reason else " (no reason)"
    count = (entry.get("options") or {}).get("count", "1")
    if kind == "member_update":
        timeout = [c for c in entry.get("changes", []) if c.get("key") == "communication_disabled_until"]
        if not timeout:
            return None
        return (f"timeout {who}{why}" if timeout[0].get("new_value")
                else f"removed timeout of {who}")
    if kind in ("kick", "ban", "unban"):
        return f"{kind} {who}{why}"
    if kind == "message_delete":
        return f"deleted {count} message(s) of {who}"
    if kind == "bulk_delete":
        return f"bulk deleted {count} messages"
    if kind == "role_update":
        return f"changed roles of {who}"
    return kind.replace("_", " ")


def windows(dates, today):
    """Messages in the last 30 days, the 30 before and the 30 before that."""
    out = [0, 0, 0]
    for d in dates:
        age = (today - d) // 86400
        if 0 <= age < 90:
            out[age // 30] += 1
    return out


def newest(rows, limit):
    out, size = [], 0
    for r in reversed(rows):
        size += len(r) + 1
        if size > limit:
            break
        out.append(r)
    return "\n".join(reversed(out))


def split(text, limit=LIMIT):
    """Splits a long report into Discord-sized messages, at line breaks."""
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def channel_messages(channel_id, after):
    """Every message after `after` (a snowflake), oldest first."""
    rows, last = [], after
    while True:
        page = call("GET", f"/channels/{channel_id}/messages?limit=100&after={last}")
        if not page:
            break
        page.sort(key=lambda m: int(m["id"]))
        rows += page
        last = page[-1]["id"]
        if len(page) < 100:
            break
    return rows


def collect(path):
    now_ms = int(time.time() * 1000)
    after = snowflake(now_ms - DAYS * 86400 * 1000)
    members = []
    last = "0"
    while True:
        page = call("GET", f"/guilds/{GUILD_ID}/members?limit=1000&after={last}") or []
        members += page
        if len(page) < 1000:
            break
        last = page[-1]["user"]["id"]
    people = {m["user"]["id"]: m for m in members if not m["user"].get("bot")}
    names = {u: m.get("nick") or m["user"].get("global_name") or m["user"]["username"]
             for u, m in people.items()}
    roles = {r["id"]: r["name"] for r in call("GET", f"/guilds/{GUILD_ID}/roles")}
    owner = call("GET", f"/guilds/{GUILD_ID}")["owner_id"]
    mods = {u for u, m in people.items() if set(m["roles"]) & set(STAFF_ROLES)}

    # Text, voice-text and announcement channels, plus active and archived threads
    chans = [c for c in call("GET", f"/guilds/{GUILD_ID}/channels") if c["type"] in (0, 2, 5, 15)]
    chan_names = {c["id"]: c["name"] for c in chans}
    threads = (call("GET", f"/guilds/{GUILD_ID}/threads/active") or {}).get("threads", [])
    for c in chans:
        if c["type"] in (0, 5, 15):
            threads += (call("GET", f"/channels/{c['id']}/threads/archived/public?limit=100")
                        or {}).get("threads", [])
    for t in threads:
        chan_names[t["id"]] = f"{chan_names.get(t.get('parent_id'), '?')} > {t['name']}"
    said = collections.defaultdict(list)
    dates = collections.defaultdict(list)
    channels = collections.defaultdict(collections.Counter)
    seen = set()
    for cid in [c["id"] for c in chans if c["type"] != 15] + [t["id"] for t in threads]:
        if cid in seen:
            continue
        seen.add(cid)
        for m in channel_messages(cid, after):
            u = m["author"]["id"]
            if u not in people:
                continue
            at = (int(m["id"]) >> 22) + EPOCH
            dates[u].append(at // 1000)
            channels[u][chan_names.get(cid, "?")] += 1
            text = " ".join(m.get("content", "").split())[:300]
            if text:
                said[u].append((at, f"[{time.strftime('%Y-%m-%d', time.gmtime(at / 1000))} "
                                    f"#{chan_names.get(cid, '?')}] {text}"))

    actions = collections.defaultdict(list)
    before = None
    while True:  # Discord keeps the audit log 45 days
        q = f"/guilds/{GUILD_ID}/audit-logs?limit=100" + (f"&before={before}" if before else "")
        page = (call("GET", q) or {}).get("audit_log_entries", [])
        for e in page:
            if e.get("user_id") in mods:
                line = describe(e, names)
                if line:
                    at = (int(e["id"]) >> 22) + EPOCH
                    actions[e["user_id"]].append(
                        f"{time.strftime('%Y-%m-%d', time.gmtime(at / 1000))} {line}")
        if len(page) < 100:
            break
        before = page[-1]["id"]

    today = int(time.time())

    def stats(u):
        w = windows(dates[u], today)
        top = ", ".join(f"{c} ({n})" for c, n in channels[u].most_common(4)) or "none"
        days = len({d // 86400 for d in dates[u]})
        last_seen = time.strftime("%Y-%m-%d", time.gmtime(max(dates[u]))) if dates[u] else "not in 90 days"
        return (f"messages per 30 days, newest first: {w[0]}, {w[1]}, {w[2]}; active on {days} days;"
                f" last message {last_seen}; most in {top}")

    out = [f"# Mod report data, {DAYS} days up to {time.strftime('%Y-%m-%d')}",
           "Member messages: private, never commit or post this file.\n"]
    out.append("## Mods")
    for u in sorted(mods, key=lambda u: -len(dates[u])):
        m = people[u]
        staff = ", ".join(STAFF_ROLES[r] for r in m["roles"] if r in STAFF_ROLES)
        out += [f"### {names[u]} ({staff}), joined {stamp(m['joined_at'])}",
                stats(u),
                "Mod actions (last 45 days): " + ("; ".join(actions[u][::-1]) or "none"),
                "Messages:", newest([t for _, t in sorted(said[u])], MOD_CHARS) or "(none)", ""]
    out.append("## Most active members who are not staff")
    picks = [u for u in sorted(people, key=lambda u: -len(dates[u]))
             if u not in mods and u != owner and dates[u]][:CANDIDATES]
    for u in picks:
        m = people[u]
        r = ", ".join(roles.get(x, "?") for x in m["roles"])
        out += [f"### {names[u]}, joined {stamp(m['joined_at'])}, roles: {r}", stats(u),
                "Messages:", newest([t for _, t in sorted(said[u])], CANDIDATE_CHARS), ""]
    with open(path, "w") as f:
        f.write("\n".join(out))
    print(f"wrote {path}: {len(mods)} mods, {len(picks)} members, "
          f"{sum(len(d) for d in dates.values())} messages from {len(seen)} channels")


def post(path):
    with open(path) as f:
        parts = split(f.read())
    for p in parts:
        call("POST", f"/channels/{MOD_TRACKER_ID}/messages",
             {"content": p, "allowed_mentions": {"parse": []}})
        time.sleep(1)
    print(f"posted {len(parts)} message(s) in MOD-TRACKER")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in ("collect", "post"):
        {"collect": collect, "post": post}[sys.argv[1]](sys.argv[2])
    else:
        print(__doc__)
