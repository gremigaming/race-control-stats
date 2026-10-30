"""Who left the server, in staff LOGS (GreMi 2026-10-30).

Discord sends the same event for leaving, a kick and a ban, so the audit log
tells them apart: a kick or ban of this member in the last minute means a mod
(or Race Control) removed them, anything else means they left on their own.
Kicks by Race Control itself are skipped: the check-in DMs log those already.

The text at the top has no Discord code, so it can be tested on its own.
"""
import asyncio
import datetime
import logging

LOG_CHANNEL = 1091494080741122148  # staff LOGS
RECENT = 60  # seconds: an audit entry older than this is about something else
log = logging.getLogger("race_control.leaves")


def how_long(seconds):
    """3 minutes, 5 hours, 12 days, 4 months, 2 years."""
    for size, unit in ((365 * 86400, "year"), (30 * 86400, "month"), (86400, "day"),
                       (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return "less than a minute"


def leave_text(how, name, username, member_id, stayed=None, roles=(), by=None, reason=None):
    """how is "left", "kick" or "ban"; stayed is seconds on the server."""
    head = {
        "left": f"🚪 **{name}** (@{username}, `{member_id}`) left the server on their own",
        "kick": f"👢 **{name}** (@{username}, `{member_id}`) was kicked",
        "ban": f"🔨 **{name}** (@{username}, `{member_id}`) was banned",
    }[how]
    if by and how != "left":
        head += f" by {by}"
    lines = [head]
    if reason and how != "left":
        lines.append(f"Reason: {reason}")
    if stayed is not None:
        lines.append(f"Was here {how_long(stayed)}")
    if roles:
        lines.append("Roles: " + ", ".join(roles))
    return "\n".join(lines)


# ---------------------------------------------------------------- Discord

async def removed_by(guild, member_id):
    """("kick" | "ban", audit entry) when a mod just removed them, else ("left", None)."""
    import discord
    await asyncio.sleep(2)  # the audit entry can land just after the event
    now = discord.utils.utcnow()
    for how, action in (("ban", discord.AuditLogAction.ban), ("kick", discord.AuditLogAction.kick)):
        async for entry in guild.audit_logs(limit=5, action=action):
            if (getattr(entry.target, "id", None) == member_id
                    and (now - entry.created_at).total_seconds() < RECENT):
                return how, entry
    return "left", None


async def on_leave(bot, guild_id, member):
    import discord
    if member.bot or member.guild.id != guild_id:
        return
    try:
        how, entry = await removed_by(member.guild, member.id)
    except discord.DiscordException as e:  # no audit log access: say so, still log it
        log.warning("audit log not read for %s: %s", member.id, e)
        how, entry = "left", None
    if entry and entry.user and entry.user.id == bot.user.id:
        return  # Race Control's own kicks are logged where they happen
    stayed = None
    if member.joined_at:
        stayed = (datetime.datetime.now(datetime.timezone.utc) - member.joined_at).total_seconds()
    roles = [r.name for r in member.roles if not r.is_default()]
    text = leave_text(how, member.display_name, member.name, member.id, stayed, roles,
                      by=entry.user.mention if entry and entry.user else None,
                      reason=entry.reason if entry else None)
    try:
        channel = bot.get_channel(LOG_CHANNEL) or await bot.fetch_channel(LOG_CHANNEL)
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.DiscordException as e:
        log.warning("leave of %s not logged: %s", member.id, e)
