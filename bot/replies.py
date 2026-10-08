"""Decides which Discord events Race Control hands to Claude. No Discord or
network code here, so it can be tested on its own.

Flow: the owner or a moderator on the allow-list tags Race Control. The bot
wakes Claude and shows typing until Claude replies. Claude answers questions directly; for
a server change it posts a plan starting with PLAN_MARK, and only carries it out
after the owner reacts to that plan with APPROVE.
"""
import json
import pathlib
import re
import unicodedata
from zoneinfo import ZoneInfo

STAFF_FILE = pathlib.Path(__file__).with_name("staff.json")
PLAN_MARK = "\U0001F4CB Plan"
REQUEST_MARK = "\U0001F4CB Change request"
CHANGE_REQUESTS_ID = 1557354121743306772  # staff only, where requests wait for GreMi's ✅
# The quick model's hand-off words. It sometimes writes "Got it! CHANGE: ...",
# so they count anywhere in the reply, not only at the start.
MARKER = re.compile(r"\b(CHANGE|RESEARCH)\s*:\s*")
# Staff asking outright for a change request, a routine or a code change: this
# always becomes a change request, whatever the quick model says.
ASKS_CHANGE = re.compile(
    r"change[- ]?request|wijzigingsverzoek|\brun (a|the|your) routine"
    r"|\b(edit|change|update|aanpassen|wijzig)\w*\b.{0,25}\b(your|je|jouw) (own )?code", re.I)
# Bot files whose change needs a restart (briefing.md and racing.md are read live)
RESTART_FILES = re.compile(r"^bot/(.+\.py|requirements\.txt)$")
TZ = ZoneInfo("Europe/Amsterdam")
APPROVE = "✅"
FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{}/fire"
FIRE_HEADERS = {
    "anthropic-beta": "experimental-cc-routine-2026-04-01",
    "anthropic-version": "2023-06-01",
    "Content-Type": "application/json",
}


def load_staff(path=STAFF_FILE):
    data = json.loads(path.read_text())
    mods = data["moderators"]
    ids = mods.values() if isinstance(mods, dict) else mods
    return int(data["owner"]), {int(i) for i in ids}


def should_handle_tag(author_id, author_is_bot, mentions_bot, owner_id, mod_ids):
    """Only allow-listed user ids count, never a role name alone."""
    return (mentions_bot and not author_is_bot
            and (author_id == owner_id or author_id in mod_ids))


def is_staff(author_id, owner_id, mod_ids):
    return author_id == owner_id or author_id in mod_ids


class Cooldown:
    """Members (not staff) get one answer per GAP seconds and PER_DAY a day."""
    GAP = 20
    PER_DAY = 30

    def __init__(self):
        self.last, self.days = {}, {}

    def allow(self, user_id, now):
        today = int(now // 86400)
        day, count = self.days.get(user_id, (today, 0))
        if day != today:
            count = 0
        if now - self.last.get(user_id, -1e9) < self.GAP or count >= self.PER_DAY:
            return False
        self.last[user_id] = now
        self.days[user_id] = (today, count + 1)
        return True


def is_approval(emoji, reactor_id, owner_id, message_author_id, bot_id, content):
    """The owner's ✅ on one of Race Control's own plan messages."""
    return (emoji == APPROVE and reactor_id == owner_id
            and message_author_id == bot_id
            and content.startswith((PLAN_MARK, REQUEST_MARK)))


def tag_body(channel_id, message_id, author_id):
    """Only ids go to Claude; Claude reads the message itself and checks the
    author again, so nothing typed in Discord can pretend to be staff."""
    return json.dumps({"text": (
        "Discord tag to handle.\n"
        f"channel={channel_id} message={message_id} author={author_id}")})


def approval_body(channel_id, plan_id, approver_id):
    return json.dumps({"text": (
        "Discord plan approved with the owner's checkmark.\n"
        f"channel={channel_id} plan={plan_id} approver={approver_id}")})


def plain(name):
    """Fancy channel names (bold letters, emoji, separators) to plain lowercase words."""
    text = unicodedata.normalize("NFKC", name)
    return "".join(c for c in text.lower() if c.isalnum() or c in " -_").strip(" -_")


def split_marker(text):
    """(kind, rest): kind is "CHANGE", "RESEARCH" or None when the quick model
    answered itself. rest is what follows the marker."""
    m = MARKER.search(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():].strip()


def change_request(requester_id, channel_id, jump_url, request):
    """The message Race Control posts in the change-requests channel."""
    request = " ".join(request.split())[:700] or "(see the linked message)"
    return (f"{REQUEST_MARK}\n"
            f"Requested by <@{requester_id}> in <#{channel_id}>: {jump_url}\n"
            f"> {request}\n"
            "GreMi, react \u2705 to approve. Claude then carries it out and replies here.")


def schedule_lines(streams, now, limit=5):
    """Upcoming streams as text for the quick model. streams: (start, end, title)
    with aware datetimes. Discord shows <t:...> in each reader's own time zone."""
    lines = []
    for start, end, title in sorted((s for s in streams if (s[1] or s[0]) > now),
                                    key=lambda s: s[0])[:limit]:
        live = " (LIVE NOW)" if start <= now else ""
        local = start.astimezone(TZ).strftime("%a %d %b %H:%M")
        lines.append(f"{local} Amsterdam time = <t:{int(start.timestamp())}:F>{live}: {title}")
    return lines


def needs_restart(changed_files):
    return any(RESTART_FILES.match(f) for f in changed_files)
