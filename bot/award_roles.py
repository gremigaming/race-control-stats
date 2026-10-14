"""The five award roles and the VIP role (GreMi 2026-10-13).

Award roles: race-stats ranks the drivers over the last 5 streams
(league/awards.json, scripts/awards.py there). Each role goes to the best
driver who has linked their Discord (bot/links.py), never GreMi (then the next
one). A holder keeps the role for at least HOLD_DAYS; after that the best
driver takes it over, and Race Control says so in F1-CHAT (old ➜ new); that line
is kept in the state file until it is really posted, so a restart can't lose it. A driver can hold
several. The roles always have a holder while anyone linked qualifies.

VIP: members whose name matches one of the Twitch channel's VIPs get the VIP
role (GreMi's own Twitch login, channel:read:vips). It never removes VIP from
anyone; differences go to the staff LOGS channel once.

The choice at the top has no Discord code, so it can be tested on its own.
"""
import asyncio
import json
import logging
import os
import pathlib
import time

from bot import links as L
from bot.members import squash

AWARDS_URL = "https://gremigaming.github.io/race-stats/league/awards.json"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_AWARDS", "/var/lib/race-control/awards.json"))
F1_CHAT_ID = 1556941705322692608
VIP_ROLE = 1100007755332452472
HOLD_DAYS = 1  # GreMi 2026-10-13: may switch every day
EVERY = 1800  # seconds between checks
AWARDS = {  # key in awards.json -> role id
    "wins": 1472160002243428487,
    "fastest": 1472160757742571675,
    "dotd": 1472161256155775026,
    "overtakes": 1472162111588270151,
    "clean": 1472162335224238144,
}
log = logging.getLogger("race_control.awards")


def choose(state, ranking, linked, present, owner_id, now, hold_days=HOLD_DAYS):
    """The holder of one award: (uid, row) or (None, None).
    state: {"uid", "since"} or {}; ranking: best first; linked: {driver lower: uid};
    present: uids on the server."""
    best = None
    for row in ranking:
        uid = linked.get(row["name"].lower())
        if uid and uid != owner_id and uid in present:
            best = (uid, row)
            break
    holder = state.get("uid")
    if holder and holder in present and holder != owner_id:
        held_for = now - state.get("since", now)
        if best is None or best[0] == holder or held_for < hold_days * 86400:
            row = next((r for r in ranking if linked.get(r["name"].lower()) == holder), None)
            return holder, row
    return best if best else (None, None)


NOTIFY_ME = 1117766461147070554  # GreMi: only ping members who have Notify Me


def wants_pings(member):
    return member is not None and any(r.id == NOTIFY_ME for r in member.roles)


def vip_matches(vips, members):
    """{member id} whose name matches a Twitch VIP. vips: [(login, display)],
    members: [(id, [names])]."""
    keys = {squash(n) for pair in vips for n in pair if n}
    keys.discard("")
    return {uid for uid, names in members if any(n and squash(n) in keys for n in names)}


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


async def fetch_awards():
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(AWARDS_URL) as r:
            return await r.json(content_type=None) if r.status == 200 else None


async def sync_awards(guild, owner_id, state):
    import discord
    data = await fetch_awards()
    if not data:
        await announce(guild, state, discord)  # still send what an earlier run missed
        return
    linked = {v["driver"].lower(): int(uid) for uid, v in L.Links().read().items()}
    present = {m.id for m in guild.members if not m.bot}
    now = time.time()
    try:
        for key, role_id in AWARDS.items():
            role = guild.get_role(role_id)
            if role is None:
                continue
            old = state.get(key, {})
            uid, row = choose(old, data["awards"].get(key, []), linked, present, owner_id, now)
            if uid is None:
                continue
            if uid != old.get("uid"):
                # The F1-CHAT line is saved together with the new holder, so a restart or a
                # failed send never loses it: announce() retries until it is posted.
                state[key] = {"uid": uid, "since": now}
                if row:
                    was = f"<@{old['uid']}> ➜ " if old.get("uid") else "➜ "
                    state.setdefault("pending", []).append(
                        {"text": f"🏆 **{role.name}**: {was}<@{uid}> ({row['text']})",
                         "uid": uid, "at": now})
                save_state(state)
            member = guild.get_member(uid)
            for m in list(role.members):
                if m.id != uid:
                    await m.remove_roles(role, reason="Award role moved to the best driver")
            if member and role not in member.roles:
                await member.add_roles(role, reason="Best over the last 5 streams")
    finally:
        await announce(guild, state, discord)


PENDING_DAYS = 2  # an announcement that could not be sent for this long is dropped


async def announce(guild, state, discord):
    """Post the saved award changes in F1-CHAT, then forget them (only once sent)."""
    pending = [p for p in state.get("pending", []) if time.time() - p["at"] < PENDING_DAYS * 86400]
    if not pending:
        if state.pop("pending", None) is not None:
            save_state(state)
        return
    channel = guild.get_channel(F1_CHAT_ID) or await guild.fetch_channel(F1_CHAT_ID)
    winners = {p["uid"] for p in pending}
    await channel.send("\n".join(p["text"] for p in pending) + "\n-# Over the last 5 streams, "
                       "checked every day. Link your driver name in "
                       "<#1558730257593409628> to compete.",
                       allowed_mentions=discord.AllowedMentions(users=[
                           discord.Object(u) for u in winners if wants_pings(guild.get_member(u))]))
    state.pop("pending", None)
    save_state(state)
    log.info("award roles: announced %d change(s) in F1-CHAT", len(pending))


async def sync_vips(guild, state, report):
    from bot import twitch_chat
    owner = twitch_chat.OWNER
    if owner is None or owner.me is None:
        return
    try:
        vips = await owner.vips()
    except Exception as e:
        if state.get("vip_error") != str(e):
            state["vip_error"] = str(e)
            save_state(state)
            await report(f"can't read the Twitch VIPs ({e}); run the Twitch chat workflow with "
                         "'login-owner' again")
        return
    state.pop("vip_error", None)
    role = guild.get_role(VIP_ROLE)
    members = [(m.id, [m.nick, m.global_name, m.name]) for m in guild.members if not m.bot]
    matched = vip_matches(vips, members)
    for uid in matched:
        m = guild.get_member(uid)
        if role and m and role not in m.roles:
            await m.add_roles(role, reason="VIP on GreMi's Twitch")
    names = {squash(n) for uid in matched for n in dict(members)[uid] if n}
    unmatched = sorted(v[1] for v in vips if squash(v[0]) not in names and squash(v[1]) not in names)
    extra = sorted(m.display_name for m in (role.members if role else []) if m.id not in matched)
    note = (f"Twitch VIPs: {len(vips)}, matched on Discord: {len(matched)}. "
            f"Not found on Discord: {', '.join(unmatched) or 'none'}. "
            f"Discord VIP but not a Twitch VIP (kept): {', '.join(extra) or 'none'}.")
    if state.get("vip_note") != note:
        state["vip_note"] = note
        save_state(state)
        await report(note)


async def run(bot, guild_id, owner_id, report):
    await bot.wait_until_ready()
    await asyncio.sleep(60)
    state = read_state()
    while True:
        guild = bot.get_guild(guild_id)
        if guild:
            for job in (sync_awards(guild, owner_id, state), sync_vips(guild, state, report)):
                try:
                    await job
                except Exception as e:  # never let this take the bot down
                    log.warning("award/VIP roles: %s", e)
        await asyncio.sleep(EVERY)
