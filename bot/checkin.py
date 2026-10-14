"""Check-in DMs for quiet members (GreMi 2026-10-14).

Members who joined more than `months` ago and haven't posted anything in that
time get one friendly DM from Race Control with two buttons:
  ✅ I'm still here   sends a welcome back with what's new (config 'welcome')
  ❌ Not anymore      shows a short "before you go" with what's new; only a
                      second no removes them (a kick, so they can rejoin)
No answer in `remind_days`: one short reminder; still nothing `remind_days`
later: removed too. Posting or joining voice in the server counts as an answer.
Members with DMs turned off are never reminded or removed. Nobody is asked twice. DMs go out a few per hour inside `hours` (Amsterdam),
at most `per_day` a day, so Discord doesn't see it as spam. Every answer, and
every DM sent, goes to LOGS.

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
LOG_CHANNEL = 1091494080741122148  # staff LOGS (GreMi 2026-10-14)
STAFF_ROLES = {1080524470332174437, 1085850009490178100}  # Moderator, Lead Moderator
YES, NO, SURE, KEEP = "checkin:yes", "checkin:no", "checkin:sure", "checkin:keep"
log = logging.getLogger("race_control.checkin")


# ---------------------------------------------------------------- decisions

def load_config(path=CONFIG):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def fresh():
    return {"asked": {}, "answers": {}, "dm": {}, "reminded": {}, "queue": [], "queue_day": None,
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


def follow_up(state, now, config, last_seen):
    """The next (member id, "remind" | "kick") for someone who got the DM and
    never answered, or None. last_seen(id) is their last message or voice time."""
    wait = config["remind_days"] * 86400
    for uid, asked in state["asked"].items():
        if uid not in state["dm"] or uid in state["answers"] or last_seen(uid) > asked:
            continue
        reminded = state["reminded"].get(uid)
        if reminded is None and now - asked >= wait:
            return uid, "remind"
        if reminded is not None and now - reminded >= wait:
            return uid, "kick"
    return None


def in_hours(now, config):
    start, end = config["hours"]
    return start <= dt.datetime.fromtimestamp(now, TZ).hour < end


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
    return (in_hours(now, config) and state["sent_today"] < config["per_day"]
            and now - state["last_sent"] >= gap(config))


def text_for(name, config):
    return "\n".join(config["text"]).replace("{name}", name)


def buttons(*items):
    return [{"type": 1, "components": [{"type": 2, "style": s, "label": label, "emoji": {"name": e},
                                        "custom_id": cid} for s, label, e, cid in items]}]


ASK = buttons((3, "I'm still here", "✅", YES), (2, "Not anymore", "❌", NO))
CONFIRM = buttons((2, "OK, I'll stay", "✅", KEEP), (4, "Yes, remove me", "👋", SURE))


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
    def __init__(self, bot, guild_id, owner_id, members=None):
        self.bot, self.guild_id, self.owner_id = bot, guild_id, owner_id
        self.members = members  # bot/members.py, for "posted or in voice since the DM"
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
            self.state["dm"][str(uid)] = channel.id
            self.state["sent_today"] += 1
            await self.report(f"📬 Check-in DM sent to {member.display_name} ({member.name}).")
        except discord.Forbidden:
            self.state["dms_off"].append(member.display_name)
            await self.report(f"📭 Check-in: {member.display_name} ({member.name}) has DMs turned off, not asked.")
        save_state(self.state)

    def last_seen(self, uid):
        if self.members is None:
            return 0
        return (self.members.data.get(str(uid)) or {}).get("last_seen", 0)

    async def follow(self, now, config):
        """One reminder or removal for someone who never answered."""
        import discord
        nxt = follow_up(self.state, now, config, self.last_seen)
        if not nxt:
            return
        uid, what = nxt
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(int(uid)) if guild else None
        if member is None:  # left by themselves
            self.state["answers"][uid] = "left"
            save_state(self.state)
            return
        if what == "remind":
            self.state["reminded"][uid] = now
            save_state(self.state)
            try:
                await self.api("POST", "/channels/{channel_id}/messages",
                               params={"channel_id": self.state["dm"][uid]},
                               json={"content": text_for(member.display_name, dict(config, text=config["reminder"])),
                                     "components": ASK, "allowed_mentions": {"parse": []}})
                await self.report(f"⏰ Check-in reminder sent to {member.display_name} ({member.name}), "
                                  "no answer for a week.")
            except discord.DiscordException as e:
                log.warning("check-in reminder failed: %s", e)
            return
        self.state["answers"][uid] = "no answer"
        save_state(self.state)
        try:
            await self.api("POST", "/channels/{channel_id}/messages", params={"channel_id": self.state["dm"][uid]},
                           json={"content": "\n".join(config["goodbye"]).replace("{invite}", config["invite"]),
                                 "allowed_mentions": {"parse": []}})
        except discord.DiscordException:
            pass
        try:
            await member.kick(reason="Check-in DM: no answer after two weeks")
            await self.report(f"👋 Check-in: {member.display_name} ({member.name}) didn't answer the DM or "
                              "the reminder in two weeks and was removed from the server.")
        except Exception as e:
            log.warning("check-in kick failed: %s", e)
            await self.report(f"⚠️ Check-in: removing {member.display_name} ({member.name}) after two weeks "
                              f"without an answer failed ({e}). Please remove them by hand.")

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
        if (not self.state["queue"] and self.state["asked"] and not self.state["done_said"]
                and self.state["queue_day"]):
            self.state["done_said"] = True
            save_state(self.state)
            await self.report(f"📬 Check-in: every quiet member has been asked ({len(self.state['asked'])}).")
        if may_send(self.state, now, config):
            await self.send_next(now, config)
        if in_hours(now, config):
            await self.follow(now, config)

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
            await self.update(interaction, original + "\n\n-# ⬇️\n" + "\n".join(config["last_chance"]), CONFIRM)
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
