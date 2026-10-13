"""Daily Discord DM to GreMi at 16:20 (Dutch time): time to post today's TikTok.

GitHub only schedules in UTC, so the "Post reminder" workflow starts twice a day
(once lines up with summer time, once with winter time). This script waits until
16:20 Amsterdam time and sends the DM; the run that doesn't match today's clock
does nothing, so there is never a second message.
"""
import datetime
import sys
import time
import zoneinfo

import post_clip

TZ = zoneinfo.ZoneInfo("Europe/Amsterdam")
AT = (16, 20)
EARLY = datetime.timedelta(minutes=60)  # start at most this long before 16:20 and wait
LATE = datetime.timedelta(minutes=25)   # GitHub can start late; still send within this
MESSAGE = ("🎬 It's 16:20, time to post today's TikTok! Open your TikTok drafts, "
           "add a sound and paste the caption from your DMs.")


def wait_seconds(now):
    """Seconds to wait before sending, or None when this run should skip."""
    target = now.replace(hour=AT[0], minute=AT[1], second=0, microsecond=0)
    if target - EARLY <= now <= target + LATE:
        return max(0.0, (target - now).total_seconds())
    return None


def main():
    wait = wait_seconds(datetime.datetime.now(TZ))
    if wait is None:
        print("Not 16:20 in the Netherlands on this run; nothing to send.")
        return
    time.sleep(wait)
    if not post_clip.DISCORD_TOKEN:
        sys.exit("DISCORD_BOT_TOKEN is missing")
    owner = post_clip.discord("GET", f"/guilds/{post_clip.GUILD_ID}")["owner_id"]
    channel = post_clip.discord("POST", "/users/@me/channels", {"recipient_id": owner})["id"]
    post_clip.discord("POST", f"/channels/{channel}/messages", {"content": MESSAGE})
    print("Reminder sent.")


if __name__ == "__main__":
    main()
