"""Watches the server for messages where the owner tags Race Control and wakes
Claude (through a Claude Code routine) to answer them.

Runs on the same schedule as the stats. Marks each tag it handed over with a
👀 reaction from the bot, so the next run doesn't hand it over again. Claude
writes the actual reply and removes the 👀.
"""
import os
import time
import urllib.parse

from update_stats import GUILD_ID, discord, http

ROUTINE_ID = os.environ.get("ROUTINE_ID", "")
ROUTINE_TOKEN = os.environ.get("ROUTINE_FIRE_TOKEN", "")
FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{}/fire"
# Only look at tags this recent (minutes); older ones were missed for good
LOOKBACK_MIN = int(os.environ.get("TAG_LOOKBACK_MIN", "30"))
EYES = "\U0001F440"
DISCORD_EPOCH_MS = 1420070400000


def snowflake_time(sid):
    return ((int(sid) >> 22) + DISCORD_EPOCH_MS) / 1000


def recent(sid, now):
    return bool(sid) and now - snowflake_time(sid) <= LOOKBACK_MIN * 60


def watched_channels(now):
    chans = [c for c in discord("GET", f"/guilds/{GUILD_ID}/channels")
             if c.get("type") in (0, 5)]
    chans += discord("GET", f"/guilds/{GUILD_ID}/threads/active").get("threads", [])
    return [c for c in chans if recent(c.get("last_message_id"), now)]


def tags_bot(msg, bot_id, bot_role_ids):
    return (any(u.get("id") == bot_id for u in msg.get("mentions", []))
            or bool(set(msg.get("mention_roles", [])) & bot_role_ids))


def handled(msg, replied):
    if msg["id"] in replied:
        return True
    return any(r.get("me") and r.get("emoji", {}).get("name") == EYES
               for r in msg.get("reactions", []))


def find_new_tags(now):
    bot_id = discord("GET", "/users/@me")["id"]
    owner_id = discord("GET", f"/guilds/{GUILD_ID}")["owner_id"]
    bot_role_ids = {r["id"] for r in discord("GET", f"/guilds/{GUILD_ID}/roles")
                    if r.get("tags", {}).get("bot_id") == bot_id}
    found = []
    for ch in watched_channels(now):
        try:
            msgs = discord("GET", f"/channels/{ch['id']}/messages?limit=25")
        except RuntimeError as e:  # a channel the bot can't read
            print(f"skipped a channel: {e}")
            continue
        replied = {m["message_reference"].get("message_id") for m in msgs
                   if m["author"]["id"] == bot_id and m.get("message_reference")}
        for m in msgs:
            if (m["author"]["id"] == owner_id and recent(m["id"], now)
                    and tags_bot(m, bot_id, bot_role_ids) and not handled(m, replied)):
                found.append((ch["id"], m["id"]))
    return found


def wake_claude(tags):
    text = "New Discord tags from Milan to answer (channel id / message id):\n" + \
        "\n".join(f"{c} / {m}" for c, m in tags)
    http("POST", FIRE_URL.format(ROUTINE_ID), {
        "Authorization": f"Bearer {ROUTINE_TOKEN}",
        "anthropic-beta": "experimental-cc-routine-2026-04-01",
        "anthropic-version": "2023-06-01",
    }, {"text": text})


def mark(tags):
    emoji = urllib.parse.quote(EYES)
    for c, m in tags:
        discord("PUT", f"/channels/{c}/messages/{m}/reactions/{emoji}/@me")


def main():
    if not (ROUTINE_ID and ROUTINE_TOKEN):
        print("ROUTINE_ID or ROUTINE_FIRE_TOKEN is missing, so tags aren't watched.")
        return
    tags = find_new_tags(time.time())
    if not tags:
        print("No new tags.")
        return
    wake_claude(tags)
    mark(tags)
    print(f"Handed {len(tags)} tag(s) to Claude.")


if __name__ == "__main__":
    main()
