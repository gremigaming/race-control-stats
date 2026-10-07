"""Activity memory per member: how much, where and when they chat or sit in voice.
Counts only, never message text. Kept on the server (never in this public repo).
No Discord code here, so it can be tested on its own.
"""
import json
import os
import pathlib
import time

PATH = pathlib.Path(os.environ.get("RACE_CONTROL_MEMBERS", "/var/lib/race-control/members.json"))
DAYS_KEPT = 60
SAVE_EVERY = 60  # seconds


def day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


class Members:
    def __init__(self, path=PATH):
        self.path = pathlib.Path(path)
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

    def message(self, user_id, channel, at=None):
        at = at or time.time()
        m = self._get(user_id)
        m["messages"] += 1
        d = day(at)
        m["days"][d] = m["days"].get(d, 0) + 1
        m["channels"][channel] = m["channels"].get(channel, 0) + 1
        m["hours"][time.gmtime(at).tm_hour] += 1
        m["last_seen"] = int(at)
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
        return "; ".join(parts) or "no activity recorded yet"
