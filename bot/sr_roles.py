"""Discord side of the safety rating: /link, /unlink and /sr, linking members
by name every few minutes, and (once switched on) the SR rank roles.
The matching and storage live in bot/links.py."""
import asyncio
import logging
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands

from bot import links as L

log = logging.getLogger("race_control.sr")
SYNC_EVERY = 600  # seconds
# GreMi approved creating the 8 rank roles on 2026-10-10
ROLES_ON = True

store = L.Links()
safety = {"data": None, "at": 0}


async def fetch_safety():
    """{driver: entry} from the site's safety.json, cached for 5 minutes."""
    if safety["data"] is None or time.time() - safety["at"] > 300:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.get(L.SAFETY_URL) as r:
                    if r.status == 200:
                        safety["data"] = await r.json(content_type=None)
                        safety["at"] = time.time()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.warning("safety.json not loaded: %s", e)
    return L.drivers_of(safety["data"])


def names_of(member):
    return [member.nick, member.global_name, member.name]


def line_for(driver, drivers):
    d = drivers.get(driver)
    if not d:
        return f"**{driver}** has no safety rating yet (only races from 7 Oct 2026 count)."
    order = [x for x in drivers.values() if not x.get("banned")]
    pos = next((i + 1 for i, x in enumerate(order) if x["name"] == driver), None)
    change = d.get("change", 0)
    sign = "+" if change > 0 else "−" if change < 0 else "±"
    extra = " · provisional until 3 races" if d.get("provisional") else ""
    return (f"**{driver}**: SR **{d['sr']:.1f}**, rank **{L.rank_of(d['sr'])}**"
            + (f" (#{pos} of {len(order)})" if pos else "")
            + f" · {sign}{abs(change):.1f} last stream{extra}\n<{L.personal_url(driver)}>")


async def set_roles(guild, uid, rank):
    """Gives the member the role of their rank and takes the other SR roles away."""
    if not ROLES_ON:
        return
    member = guild.get_member(uid)
    if member is None:
        return
    roles = await ensure_roles(guild)
    want = roles.get(rank) if rank else None
    remove = [r for r in member.roles if r.name.startswith(L.ROLE_PREFIX) and r != want]
    if remove:
        await member.remove_roles(*remove, reason="Safety rating changed")
    if want and want not in member.roles:
        await member.add_roles(want, reason="Safety rating")


async def ensure_roles(guild):
    have = {r.name: r for r in guild.roles}
    out = {}
    for rank, _, colour in L.RANKS:
        name = L.role_name(rank)
        role = have.get(name)
        if role is None:
            role = await guild.create_role(name=name, colour=discord.Colour(colour), hoist=False,
                                           mentionable=False, reason="Safety rating ranks")
        out[rank] = role
    return out


async def sync(guild):
    drivers = await fetch_safety()
    if not drivers:
        return
    members = [(m.id, names_of(m)) for m in guild.members if not m.bot]
    new = store.auto(members, list(drivers))
    for uid, d in new:
        log.info("linked %s to %s by name", uid, d)
    if ROLES_ON:
        for uid, rank in L.wanted_roles(store.read(), drivers).items():
            try:
                await set_roles(guild, uid, rank)
            except discord.DiscordException as e:
                log.warning("SR role for %s not set: %s", uid, e)


async def sync_loop(bot, guild_id):
    await asyncio.sleep(30)
    while True:
        try:
            guild = bot.get_guild(guild_id)
            if guild:
                await sync(guild)
        except Exception as e:  # never let this take the bot down
            log.warning("SR sync failed: %s", e)
        await asyncio.sleep(SYNC_EVERY)


def setup(bot, guild_id):
    """Adds the commands to the bot; call setup_ready(bot) from on_ready."""
    tree = app_commands.CommandTree(bot)
    server = discord.Object(id=guild_id)

    async def driver_choices(interaction, current):
        drivers = await fetch_safety()
        cur = current.lower()
        names = sorted((n for n in drivers if cur in n.lower()), key=lambda n: (not n.lower().startswith(cur), n.lower()))
        return [app_commands.Choice(name=n, value=n) for n in names[:25]]

    @tree.command(name="link", description="Link your Discord to your driver name for the safety rating", guild=server)
    @app_commands.describe(driver="Your name in the game, as it shows on the race stats site")
    @app_commands.autocomplete(driver=driver_choices)
    async def link(interaction: discord.Interaction, driver: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        drivers = await fetch_safety()
        name = next((n for n in drivers if n.lower() == driver.strip().lower()), None)
        if not name:
            await interaction.followup.send(
                f"I don't know the driver **{discord.utils.escape_markdown(driver)}** yet. Pick a name from the list; "
                "you're on it once you've raced with us since 7 Oct 2026.", ephemeral=True)
            return
        ok, msg = store.link(interaction.user.id, name, "self")
        if ok:
            msg += "\n" + line_for(name, drivers)
            if interaction.guild:
                try:
                    await set_roles(interaction.guild, interaction.user.id, L.wanted_roles(
                        {str(interaction.user.id): {"driver": name}}, drivers)[interaction.user.id])
                except discord.DiscordException as e:
                    log.warning("SR role not set after /link: %s", e)
        await interaction.followup.send(msg, ephemeral=True)

    @tree.command(name="unlink", description="Remove the link between your Discord and your driver name", guild=server)
    async def unlink(interaction: discord.Interaction):
        done = store.unlink(interaction.user.id)
        if done and interaction.guild:
            try:
                await set_roles(interaction.guild, interaction.user.id, None)
            except discord.DiscordException as e:
                log.warning("SR role not removed after /unlink: %s", e)
        await interaction.response.send_message(
            "Unlinked. Use /link to pick your name again." if done else "You weren't linked to a driver.",
            ephemeral=True)

    @tree.command(name="sr", description="Show a safety rating", guild=server)
    @app_commands.describe(member="Whose rating (yours if empty)")
    async def sr(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        who = member or interaction.user
        link = store.get(who.id)
        if not link:
            text = ("You're not linked to a driver yet. Use /link to pick your name."
                    if who == interaction.user else f"{who.display_name} isn't linked to a driver yet.")
            await interaction.response.send_message(text, ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        drivers = await fetch_safety()
        await interaction.followup.send(line_for(link["driver"], drivers))

    bot.sr_tree = tree


async def setup_ready(bot, guild_id):
    """Registers the commands with the server and starts the sync loop, once."""
    if getattr(bot, "sr_started", False):
        return
    bot.sr_started = True
    try:
        await bot.sr_tree.sync(guild=discord.Object(id=guild_id))
    except Exception as e:
        log.warning("slash commands not registered: %s", e)
    asyncio.create_task(sync_loop(bot, guild_id))
