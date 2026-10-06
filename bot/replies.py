"""Decides which Discord events Race Control hands to Claude. No Discord or
network code here, so it can be tested on its own.

Flow: the owner or a moderator on the allow-list tags Race Control. The bot
wakes Claude and shows typing until Claude replies. Claude answers questions directly; for
a server change it posts a plan starting with PLAN_MARK, and only carries it out
after the owner reacts to that plan with APPROVE.
"""
import json
import pathlib

STAFF_FILE = pathlib.Path(__file__).with_name("staff.json")
PLAN_MARK = "\U0001F4CB Plan"
APPROVE = "✅"
FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{}/fire"
FIRE_HEADERS = {
    "anthropic-beta": "experimental-cc-routine-2026-04-01",
    "anthropic-version": "2023-06-01",
    "Content-Type": "application/json",
}


def load_staff(path=STAFF_FILE):
    data = json.loads(path.read_text())
    return int(data["owner"]), {int(i) for i in data["moderators"].values()}


def should_handle_tag(author_id, author_is_bot, mentions_bot, owner_id, mod_ids):
    """Only allow-listed user ids count, never a role name alone."""
    return (mentions_bot and not author_is_bot
            and (author_id == owner_id or author_id in mod_ids))


def is_approval(emoji, reactor_id, owner_id, message_author_id, bot_id, content):
    """The owner's ✅ on one of Race Control's own plan messages."""
    return (emoji == APPROVE and reactor_id == owner_id
            and message_author_id == bot_id and content.startswith(PLAN_MARK))


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
