"""Activity memory per member: how much, where and when they chat or sit in voice,
plus an archive of message text (one file per day, kept ARCHIVE_DAYS days).
Everything stays on the server, never in this public repo.
No Discord code here, so it can be tested on its own.

Delete someone's data when they ask (on the server; the bot must be stopped,
or it saves its copy of their stats again):
  cd /opt/race-control && sudo systemctl stop race-control &&
  sudo .venv/bin/python -m bot.members forget <discord user id>; sudo systemctl start race-control
"""
import json
import os
import pathlib
import re
import time

from bot.replies import plain

PATH = pathlib.Path(os.environ.get("RACE_CONTROL_MEMBERS", "/var/lib/race-control/members.json"))
GUILD_ID = 1079917337165172876  # the real GreMi_Gaming server, not the test one
DAYS_KEPT = 60
ARCHIVE = pathlib.Path(os.environ.get("RACE_CONTROL_ARCHIVE", "/var/lib/race-control/messages"))
ARCHIVE_DAYS = 90
SAVE_EVERY = 60  # seconds
SUMMARY_CHARS = 4000  # newest message text sent when writing a profile (~1000 tokens)
SUMMARY_MAX = 300     # characters kept per profile


LEET = str.maketrans("013457", "oieast")


def squash(name):
    """'TTV_H1ddegam1ng' and '[QDR] Shw1ks' to 'hiddegaming' and 'shwiks'."""
    name = plain(re.sub(r"^\[[^\]]*\]\s*", "", name)).translate(LEET)
    for tag in ("ttv_", "ttv "):
        name = name.removeprefix(tag)
    return "".join(c for c in name if c.isalpha())


def named_members(text, members):
    """Members whose name shows up in the text (at most 2)."""
    flat = squash(text)
    words = {squash(w) for w in text.split()}
    found = []
    for m in members:
        keys = {k for k in (squash(m.display_name), squash(m.name)) if len(k) >= 3}
        # Short names like "nex" only as a whole word, longer ones anywhere
        if any(k in words if len(k) < 5 else k in flat for k in keys) or any(
                len(w) >= 5 and any(k.startswith(w) for k in keys) for w in words):
            found.append(m)
            if len(found) == 2:
                break
    return found


def day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


class Members:
    def __init__(self, path=PATH, archive=ARCHIVE):
        self.path = pathlib.Path(path)
        self.archive_dir = pathlib.Path(archive)
        self.pruned = ""
        self.saved_at = time.time()
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.in_voice = {}  # user id -> joined at

    def _get(self, user_id):
        return self.data.setdefault(str(user_id), {
            "messages": 0, "days": {}, "channels": {}, "hours": [0] * 24,
            "voice_minutes": 0, "first_seen": int(time.time()), "last_seen": 0})

    def backfilled(self):
        return (self.path.parent / "backfilled").exists()

    def mark_backfilled(self):
        try:
            (self.path.parent / "backfilled").touch()
        except OSError:
            pass

    def archive(self, message_id, user_id, channel, text, at=None):
        at = at or time.time()
        row = {"id": str(message_id), "at": int(at), "user": str(user_id),
               "channel": channel, "text": text}
        try:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            with open(self.archive_dir / f"{day(at)}.jsonl", "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            return
        if self.pruned != day(at):  # once a day, drop files past ARCHIVE_DAYS
            self.pruned = day(at)
            oldest = day(at - ARCHIVE_DAYS * 86400)
            for f in self.archive_dir.glob("*.jsonl"):
                if f.stem < oldest:
                    f.unlink(missing_ok=True)

    def forget(self, user_id):
        """Removes everything kept about one member: stats and archived messages."""
        self.data.pop(str(user_id), None)
        self.maybe_save(force=True)
        removed = 0
        for f in sorted(self.archive_dir.glob("*.jsonl")):
            lines = f.read_text().splitlines()
            keep = [l for l in lines if json.loads(l).get("user") != str(user_id)]
            if len(keep) != len(lines):
                removed += len(lines) - len(keep)
                f.write_text("".join(l + "\n" for l in keep))
        return removed

    def message(self, user_id, channel, at=None):
        at = at or time.time()
        m = self._get(user_id)
        m["messages"] += 1
        d = day(at)
        m["days"][d] = m["days"].get(d, 0) + 1
        m["channels"][channel] = m["channels"].get(channel, 0) + 1
        m["hours"][time.gmtime(at).tm_hour] += 1
        m["last_seen"] = max(m["last_seen"], int(at))
        m["first_seen"] = min(m["first_seen"], int(at))
        self._trim(m, at)
        self.maybe_save()

    def voice(self, user_id, joined, at=None):
        """Call with joined=True when someone enters voice, False when they leave."""
        at = at or time.time()
        if joined:
            self.in_voice.setdefault(user_id, at)
            return
        start = self.in_voice.pop(user_id, None)
        if start is not None:
            m = self._get(user_id)
            m["voice_minutes"] += round((at - start) / 60)
            m["last_seen"] = int(at)
            self.maybe_save()

    @staticmethod
    def _trim(m, at):
        oldest = day(at - DAYS_KEPT * 86400)
        for d in [d for d in m["days"] if d < oldest]:
            del m["days"][d]

    def maybe_save(self, force=False):
        if not force and time.time() - self.saved_at < SAVE_EVERY:
            return
        self.saved_at = time.time()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
        except OSError:
            pass

    def top(self, n=5, days=7, now=None):
        """[(user id, messages)] for the most active members of the last days."""
        since = day((now or time.time()) - days * 86400)
        counts = {u: sum(c for d, c in m["days"].items() if d >= since) for u, m in self.data.items()}
        return sorted(((u, c) for u, c in counts.items() if c), key=lambda uc: -uc[1])[:n]

    def profile(self, user_id, roles=(), joined=None, now=None):
        """One short line about a member for Race Control's prompt."""
        now = now or time.time()
        m = self.data.get(str(user_id))
        parts = []
        if m:
            week = sum(n for d, n in m["days"].items() if d >= day(now - 7 * 86400))
            parts.append(f"{m['messages']} messages since {day(m['first_seen'])}, {week} this week")
            top = sorted(m["channels"].items(), key=lambda kv: -kv[1])[:3]
            if top:
                parts.append("most in " + ", ".join(f"{c} ({n})" for c, n in top))
            if any(m["hours"]):
                h = max(range(24), key=lambda i: m["hours"][i])
                parts.append(f"usually around {h:02d}:00 UTC")
            if m["voice_minutes"]:
                parts.append(f"{m['voice_minutes']} min in voice")
        if roles:
            parts.append("roles " + ", ".join(roles))
        if joined:
            parts.append(f"joined {joined}")
        if m and m.get("summary"):
            parts.append(f"profile: {m['summary']}")
        return "; ".join(parts) or "no activity recorded yet"

    def said_since(self, user_id, since, limit=SUMMARY_CHARS):
        """A member's archived messages after `since`, newest kept when over limit."""
        rows = []
        for f in sorted(self.archive_dir.glob("*.jsonl")):
            if f.stem < day(since):
                continue
            for line in f.read_text().splitlines():
                r = json.loads(line)
                if r.get("user") == str(user_id) and r["at"] > since and r["text"].strip():
                    rows.append(f"[{r['channel']}] {r['text'][:200]}")
        out, size = [], 0
        for r in reversed(rows):
            size += len(r) + 1
            if size > limit:
                break
            out.append(r)
        return "\n".join(reversed(out))

    def due_for_summary(self):
        """Members who wrote something since their profile was last written."""
        return [u for u, m in self.data.items() if m["last_seen"] > m.get("summarized_at", 0)]

    def set_summary(self, user_id, summary, at=None):
        m = self._get(user_id)
        m["summary"] = summary[:SUMMARY_MAX]
        m["summarized_at"] = int(at or time.time())


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "forget":
        print(f"removed stats and {Members().forget(sys.argv[2])} archived messages")
    else:
        print("usage: python -m bot.members forget <discord user id>")
