"""Check-in DMs for quiet members (GreMi 2026-10-14).

Members who joined more than `months` ago and haven't posted anything in that
time get one friendly DM from Race Control with two buttons:
  ✅ I'm still here   sends a welcome back with what's new (config 'welcome')
  ❌ Not anymore      asks "Sure?" first; confirming removes them (a kick, so
                      they can rejoin with an invite)
Nobody is asked twice. DMs go out a few per hour inside `hours` (Amsterdam),
at most `per_day` a day, so Discord doesn't see it as spam. Every answer, and
a daily line with who has DMs turned off, goes to MOD-ALERTS.

bot/checkin.json is read live; "on": false stops sending (buttons keep working).
State lives on the server only (STATE), never in this public repo.
The decisions at the top have no Discord code, so they can be tested on their own.
"""
import asyncio
import datetime as dt
import json
import logging
import os
import pathlib
import time
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = HERE / "checkin.json"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_CHECKIN", "/var/lib/race-control/checkin.json"))
TZ = ZoneInfo("Europe/Amsterdam")
TICK = 60                # seconds between checks
LOG_CHANNEL = 1559833714819727372  # 🚨┃MOD-ALERTS
STAFF_ROLES = {1080524470332174437, 1085850009490178100}  # Moderator, Lead Moderator
YES, NO, SURE, KEEP = "checkin:yes", "checkin:no", "checkin:sure", "checkin:keep"
log = logging.getLogger("race_control.checkin")


# ---------------------------------------------------------------- decisions

def load_config(path=CONFIG):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def fresh():
    return {"asked": {}, "answers": {}, "queue": [], "queue_day": None,
            "day": None, "sent_today": 0, "last_sent": 0, "dms_off": [], "done_said": False}


def cutoff(now, config):
    return now - config["months"] * 30.5 * 86400


def candidates(members, last_post, now, config, owner_id, asked):
    """Ids of the quiet members, longest in the server first.
    members: [{"id", "joined" (unix), "bot", "roles"}]; last_post: {id: unix}."""
    cut = cutoff(now, config)
    quiet = [m for m in members
             if not m["bot"] and m["id"] != owner_id and not (set(m["roles"]) & STAFF_ROLES)
             and m["joined"] < cut and last_post.get(m["id"], 0) < cut and str(m["id"]) not in asked]
    return [m["id"] for m in sorted(quiet, key=lambda m: m["joined"])]


def gap(config):
    start, end = config["hours"]
    return (end - start) * 3600 / config["per_day"]


def new_day(state, now):
    """Starts a new day's count. Returns the finished day's numbers, or None."""
    today = dt.datetime.fromtimestamp(now, TZ).date().isoformat()
    if state["day"] == today:
        return None
    done = {"day": state["day"], "sent": state["sent_today"], "dms_off": state["dms_off"]}
    state.update(day=today, sent_today=0, dms_off=[])
    return done if done["day"] else None


def may_send(state, now, config):
    if not config.get("on") or not state["queue"]:
        return False
    hour = dt.datetime.fromtimestamp(now, TZ).hour
    start, end = config["hours"]
    return (start <= hour < end and state["sent_today"] < config["per_day"]
            and now - state["last_sent"] >= gap(config))


def text_for(name, config):
    return "\n".join(config["text"]).replace("{name}", name)


def buttons(*items):
    return [{"type": 1, "components": [{"type": 2, "style": s, "label": label, "emoji": {"name": e},
                                        "custom_id": cid} for s, label, e, cid in items]}]


ASK = buttons((3, "I'm still here", "✅", YES), (2, "Not anymore", "❌", NO))
CONFIRM = buttons((4, "Yes, remove me", "👋", SURE), (2, "No, keep me", "✅", KEEP))


def read_state():
    try:
        return dict(fresh(), **json.loads(STATE.read_text()))
    except (OSError, ValueError):
        return fresh()


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


# ---------------------------------------------------------------- Discord

class Runner:
    def __init__(self, bot, guild_id, owner_id):
        self.bot, self.guild_id, self.owner_id = bot, guild_id, owner_id
        self.state = read_state()

    async def api(self, method, path, **kw):
        from discord.http import Route
        return await self.bot.http.request(Route(method, path, **kw.pop("params", {})), **kw)

    async def report(self, text):
        try:
            await self.api("POST", "/channels/{channel_id}/messages", params={"channel_id": LOG_CHANNEL},
                           json={"content": text, "allowed_mentions": {"parse": []}})
        except Exception as e:
            log.warning("check-in report failed: %s", e)

    async def last_posts(self, guild, since):
        """{member id: newest message time} over every channel and thread since `since`."""
        import discord
        after = dt.datetime.fromtimestamp(since, dt.timezone.utc)
        places = [c for c in guild.channels if isinstance(c, (discord.TextChannel, discord.VoiceChannel))]
        places += list(guild.threads)
        for c in guild.channels:
            if isinstance(c, (discord.TextChannel, discord.ForumChannel)):
                try:
                    places += [t async for t in c.archived_threads(limit=100)]
                except discord.DiscordException:
                    pass
        last = {}
        for place in {p.id: p for p in places}.values():
            try:
                async for m in place.history(limit=None, after=after):
                    last[m.author.id] = max(last.get(m.author.id, 0), m.created_at.timestamp())
            except discord.DiscordException:
                pass  # a channel the bot can't read
        return last

    async def refresh_queue(self, now, config):
        """Once a day: who is still quiet and not asked yet."""
        today = dt.datetime.fromtimestamp(now, TZ).date().isoformat()
        if self.state["queue_day"] == today:
            return
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return
        last = await self.last_posts(guild, cutoff(now, config))
        members = [{"id": m.id, "joined": m.joined_at.timestamp() if m.joined_at else now,
                    "bot": m.bot, "roles": [r.id for r in m.roles]} for m in guild.members]
        self.state["queue"] = candidates(members, last, now, config, self.owner_id, self.state["asked"])
        self.state["queue_day"] = today
        save_state(self.state)
        log.info("check-in: %d quiet members waiting", len(self.state["queue"]))

    async def send_next(self, now, config):
        import discord
        uid = self.state["queue"].pop(0)
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(uid) if guild else None
        if member is None:  # left the server already
            save_state(self.state)
            return
        self.state["asked"][str(uid)] = now
        self.state["last_sent"] = now
        try:
            channel = await member.create_dm()
            await self.api("POST", "/channels/{channel_id}/messages", params={"channel_id": channel.id},
                           json={"content": text_for(member.display_name, config), "components": ASK,
                                 "allowed_mentions": {"parse": []}})
            self.state["sent_today"] += 1
        except discord.Forbidden:
            self.state["dms_off"].append(member.display_name)
        save_state(self.state)

    async def step(self):
        config, now = load_config(), time.time()
        done = new_day(self.state, now)
        if done and (done["sent"] or done["dms_off"]):
            off = f" DMs turned off, not asked: {', '.join(done['dms_off'])}." if done["dms_off"] else ""
            await self.report(f"📬 Check-in {done['day']}: {done['sent']} DMs sent, "
                              f"{len(self.state['queue'])} still to go.{off}")
        save_state(self.state)
        if not config.get("on"):
            return
        await self.refresh_queue(now, config)
        if not self.state["queue"] and self.state["asked"] and not self.state["done_said"]:
            self.state["done_said"] = True
            save_state(self.state)
            await self.report(f"📬 Check-in: every quiet member has been asked ({len(self.state['asked'])}).")
        if may_send(self.state, now, config):
            await self.send_next(now, config)

    async def run(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.step()
            except Exception as e:  # never let the check-in take the bot down
                log.warning("check-in step failed: %s", e)
            await asyncio.sleep(TICK)

    async def update(self, interaction, content, components=()):
        """Answers a click by editing the DM in place (text and buttons)."""
        await self.api("POST", "/interactions/{interaction_id}/{token}/callback",
                       params={"interaction_id": interaction.id, "token": interaction.token},
                       json={"type": 7, "data": {"content": content, "components": list(components),
                                                 "allowed_mentions": {"parse": []}}})

    async def on_click(self, interaction):
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("checkin:"):
            return
        user, config = interaction.user, load_config()
        uid = str(user.id)
        original = interaction.message.content.split("\n\n-# ")[0] if interaction.message else ""
        if cid == NO:
            await self.update(interaction, original + "\n\n-# Sure? This removes you from the server. "
                              f"You can always come back with an invite: {config['invite']}", CONFIRM)
            return
        if cid in (YES, KEEP):
            self.state["answers"][uid] = "yes"
            save_state(self.state)
            await self.update(interaction, original + "\n\n-# ✅ You're still here.")
            await self.api("POST", "/channels/{channel_id}/messages",
                           params={"channel_id": interaction.channel_id},
                           json={"content": "\n".join(config["welcome"]), "allowed_mentions": {"parse": []}})
            await self.report(f"✅ Check-in: {user.display_name} ({user.name}) is still here.")
            return
        # SURE: they confirmed they want to leave
        self.state["answers"][uid] = "left"
        save_state(self.state)
        await self.update(interaction, original + "\n\n-# 👋 Done. Thanks for having been part of The GreMi "
                                                  f"Gang! If you ever want to come back: {config['invite']}")
        if user.id == self.owner_id:
            return  # GreMi testing the DM: never remove the owner
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(user.id) if guild else None
        if member is None:
            await self.report(f"👋 Check-in: {user.display_name} ({user.name}) is no longer interested "
                              "(already left the server).")
            return
        try:
            await member.kick(reason="Check-in DM: said they're no longer interested")
            await self.report(f"👋 Check-in: {user.display_name} ({user.name}) is no longer interested "
                              "and was removed from the server.")
        except Exception as e:
            log.warning("check-in kick failed: %s", e)
            await self.report(f"⚠️ Check-in: {user.display_name} ({user.name}) asked to be removed, "
                              f"but the kick failed ({e}). Please remove them by hand.")
