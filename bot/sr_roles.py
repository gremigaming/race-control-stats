"""Discord side of the safety rating: /link, /unlink and /sr, linking members
by name every few minutes, and the SR rank roles. The matching and storage
live in bot/links.py. The championship and safety rating pictures in their
channels come from leaderboard_post.py."""
import asyncio
import logging
import re
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

# F1 channels GreMi asked for on 2026-10-11, in the RACING category
PANEL_CHANNEL_ID = 1558730257593409628  # link-your-name: the "pick your driver name" panel
PANEL_TITLE = "Link your driver name"
PICK = "srlink:pick:"  # + driver name (cut to 88): a name button under the form's answer
OPEN = "srlink:open"  # "Type my name": opens the form
FORM = "srlink:form"
FORM_NAME = "srlink:name"
UNLINK = "srlink:unlink"
GROUP = "srlink:group"  # the letter dropdown on the panel
NAMES = "srlink:names:"  # + i: a dropdown of names in the private answer to a letter pick
GROUP_SIZE = 25  # names per letter group, so each group usually fits one dropdown

store = L.Links()
CLICKS = []  # more async fn(interaction) for buttons elsewhere (bot/giveaway.py)
safety = {"data": None, "at": 0}


async def load(cache, url):
    """The site's JSON at url, cached for 5 minutes."""
    if cache["data"] is None or time.time() - cache["at"] > 300:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.get(url) as r:
                    if r.status == 200:
                        cache["data"] = await r.json(content_type=None)
                        cache["at"] = time.time()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.warning("%s not loaded: %s", url, e)
    return cache["data"]


async def fetch_safety():
    """{driver: entry} from the site's safety.json."""
    return L.drivers_of(await load(safety, L.SAFETY_URL))


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


def first_letter(name):
    """A to Z, or # for a name that starts with a digit or symbol (clan tags and
    TTV/ in front don't count)."""
    k = L.key(name) or L.full(name)
    return k[0].upper() if k and k[0].isalpha() else "#"


def letter_groups(drivers):
    """[(label, [names])]: the drivers A to Z in groups of letters, each about
    GROUP_SIZE names (a big letter gets a group of its own), at most 25 groups."""
    names = [n for n, d in drivers.items() if not d.get("banned") and not L.HIDDEN.search(n)]
    by = {}
    for n in sorted(names, key=lambda n: (L.key(n) or L.full(n), n.lower())):
        by.setdefault(first_letter(n), []).append(n)
    letters = sorted(by, key=lambda c: (c != "#", c))
    size = GROUP_SIZE
    while True:
        groups, cur = [], []
        for c in letters:
            if cur and sum(len(by[x]) for x in cur) + len(by[c]) > size:
                groups.append(cur)
                cur = []
            cur.append(c)
        if cur:
            groups.append(cur)
        if len(groups) <= 25:
            break
        size *= 2
    def label(g):
        digits, g = g[0] == "#", [c for c in g if c != "#"]
        letters = "" if not g else g[0] if len(g) == 1 else f"{g[0]} to {g[-1]}"
        return ", ".join(x for x in ("0-9" if digits else "", letters) if x)
    return [(label(g), [n for c in g for n in by[c]]) for g in groups]


def panel(drivers):
    """The panel: one dropdown of letter groups; a pick opens a private dropdown
    with the names in that group (a dropdown holds 25 names at most, so this
    works for any number of drivers)."""
    groups = letter_groups(drivers)
    count = sum(len(g) for _, g in groups)
    embed = discord.Embed(
        title=PANEL_TITLE, colour=0x4ADE80,
        description=(
            "**What is this?**\n"
            "Every F1 race in our lobby is saved on the race stats site. Each driver gets a safety rating (SR): "
            "clean races and clean overtakes raise it, contacts, penalties and retirements lower it.\n\n"
            "**Why link?**\n"
            "The site knows your name in the game, not your Discord. Linking tells us which driver you are, so:\n"
            "• you get the SR role of your rank (S+ down to F) here on the server\n"
            "• `/sr` shows your rating and your place in the ranking\n"
            "• your role follows your rating by itself after every stream\n\n"
            "**How?**\n"
            "1. Pick the first letter of your name as it shows in the game in the menu below.\n"
            "2. Pick your name in the list that opens. Type in it to filter.\n"
            "Only you see the answer. Clan tags and TTV/ in front don't count for the letter.\n\n"
            "Can't find it? Press **Type my name**. Not on the site yet? Your name shows up after your "
            "first race with us. Picked the wrong one? Pick again, or press Unlink me. "
            "Someone else has your name? Ask a mod."))
    embed.set_footer(text=f"{count} drivers · 🔗 means the name is already linked")
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Select(
        custom_id=GROUP, placeholder="1. Pick the first letter of your driver name",
        options=[discord.SelectOption(
            label=f"{label}  ({len(g)} driver{'s' if len(g) != 1 else ''})"[:100], value=label,
            description=", ".join(g)[:97] + ("..." if len(", ".join(g)) > 97 else ""))
            for label, g in groups] or [discord.SelectOption(label="No drivers yet", value="-")]))
    view.add_item(discord.ui.Button(label="Unlink me", style=discord.ButtonStyle.secondary, custom_id=UNLINK))
    view.add_item(discord.ui.Button(label="Type my name", style=discord.ButtonStyle.secondary,
                                    emoji="\u270F\uFE0F", custom_id=OPEN))
    view.add_item(discord.ui.Button(label="Full ranking", style=discord.ButtonStyle.link, url=L.SITE_URL))
    return embed, view


def linked_to(guild):
    """{driver name lowercased: member display name} for the name dropdowns."""
    out = {}
    for uid, v in store.read().items():
        m = guild.get_member(int(uid)) if guild else None
        out[v["driver"].lower()] = m.display_name if m else "a member who left"
    return out


def names_view(names, linked):
    """The private answer to a letter pick: the names in dropdowns of 25 (5 at most)."""
    view = discord.ui.View(timeout=900)  # picks are read in on_panel, also after this
    chunks = [names[i:i + 25] for i in range(0, min(len(names), 125), 25)]
    for i, g in enumerate(chunks):
        view.add_item(discord.ui.Select(
            custom_id=f"{NAMES}{i}",
            placeholder=("2. Pick your name (type to filter)" if len(chunks) == 1
                         else f"2. Pick your name: {g[0][:20]} to {g[-1][:20]}"),
            options=[discord.SelectOption(
                label=n[:100], value=n[:100], emoji="\U0001F517" if n.lower() in linked else None,
                description=(f"Linked to {linked[n.lower()]}"[:100] if n.lower() in linked else None))
                for n in g]))
    return view


def signature(rows):
    """What a panel's buttons are, to see whether a posted panel is out of date."""
    return [(getattr(c, "custom_id", None), getattr(c, "url", None), getattr(c, "label", None),
             [(o.label, o.value, o.description) for o in getattr(c, "options", None) or []])
            for row in rows for c in getattr(row, "children", [row])]


async def keep_panel(guild, drivers):
    """Posts the panel once, and edits it when its text or buttons changed (the
    old dropdown panel becomes the button panel the same way)."""
    channel = guild.get_channel(PANEL_CHANNEL_ID)
    if channel is None:
        return
    embed, view = panel(drivers)
    async for msg in channel.history(limit=50):
        if msg.author == guild.me and msg.embeds and msg.embeds[0].title == PANEL_TITLE:
            old = msg.embeds[0]
            if (signature(msg.components) != signature(view.children)
                    or old.description != embed.description or old.footer.text != embed.footer.text):
                await msg.edit(embed=embed, view=view)
            return
    await channel.send(embed=embed, view=view)


class NameForm(discord.ui.Modal, title="Link your driver name"):
    """The form behind "Type my name". Its answer is read in on_panel (raw), so a
    bot restart while someone types doesn't lose it."""
    name = discord.ui.TextInput(label="Your name in the game", custom_id=FORM_NAME, min_length=1,
                                max_length=100, placeholder="As it shows in the race results")

    def __init__(self):
        super().__init__(custom_id=FORM, timeout=600)

    async def interaction_check(self, interaction):
        return False  # on_panel answers


def typed_value(data):
    """The text typed in the form, from the raw modal answer (old and new layouts)."""
    stack = list((data or {}).get("components") or [])
    while stack:
        c = stack.pop(0)
        if c.get("custom_id") == FORM_NAME and "value" in c:
            return c.get("value") or ""
        stack[:0] = list(c.get("components") or []) + ([c["component"]] if c.get("component") else [])
    return ""


def choices_view(names, uid):
    """Buttons for the names that look like what was typed, plus "Type again"."""
    data = store.read()
    view = discord.ui.View(timeout=900)  # clicks are read in on_panel, also after this
    for n in names:
        owner = store.owner(n, data)
        mine = owner == uid
        taken = owner is not None and not mine and data[str(owner)]["how"] != "auto"
        label = n[:70] + (" (you)" if mine else " (taken)" if taken else "")
        view.add_item(discord.ui.Button(label=label, custom_id=PICK + n[:88],
                                        style=discord.ButtonStyle.secondary if taken or mine
                                        else discord.ButtonStyle.primary))
    view.add_item(discord.ui.Button(label="Type again", emoji="\u270F\uFE0F", custom_id=OPEN,
                                    style=discord.ButtonStyle.secondary))
    return view


async def link_member(interaction, driver, drivers):
    """The /link and panel answer: links, sets the role, says the rating."""
    ok, msg = store.link(interaction.user.id, driver, "self")
    if ok:
        msg += "\n" + line_for(driver, drivers)
        if interaction.guild:
            try:
                await set_roles(interaction.guild, interaction.user.id, L.wanted_roles(
                    {str(interaction.user.id): {"driver": driver}}, drivers)[interaction.user.id])
            except discord.DiscordException as e:
                log.warning("SR role not set after linking: %s", e)
    return msg


def known(drivers, picked):
    """The driver a name button or old dropdown meant (button ids hold the name cut to 88)."""
    if not picked:
        return None
    return next((n for n in drivers if n == picked), None) or next(
        (n for n in drivers if n[:88] == picked or n[:100] == picked), None)


async def on_form(interaction):
    """The typed name: links straight away when it is a driver's name, else shows
    the closest names as buttons."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    typed = typed_value(interaction.data).strip()
    drivers = await fetch_safety()
    if not drivers:
        await interaction.followup.send("The race stats site didn't answer. Try again in a minute.", ephemeral=True)
        return
    names = L.suggest(typed, list(drivers))
    shown = discord.utils.escape_markdown(typed)
    if len(names) == 1 and L.is_name(typed, names[0]):
        await interaction.followup.send(await link_member(interaction, names[0], drivers), ephemeral=True)
        return
    if not names:
        await interaction.followup.send(
            f"I can't find a driver called **{shown}**. Check how it shows in the race results, or type "
            "part of it. You're in the list once you've raced with us since 7 Oct 2026.",
            view=choices_view([], interaction.user.id), ephemeral=True)
        return
    await interaction.followup.send(f"No driver is called exactly **{shown}**. Which one are you?",
                                    view=choices_view(names, interaction.user.id), ephemeral=True)


async def on_panel(interaction):
    """The letter dropdown and buttons on the panel, the private name dropdowns,
    the form and the name buttons under the form's answer."""
    cid = (interaction.data or {}).get("custom_id", "")
    if not cid.startswith("srlink:"):
        return
    if interaction.type == discord.InteractionType.modal_submit:
        if cid == FORM:
            await on_form(interaction)
        return
    if interaction.type != discord.InteractionType.component:
        return
    if cid == OPEN:
        await interaction.response.send_modal(NameForm())
        return
    values = (interaction.data or {}).get("values")
    if cid.startswith(PICK) and not values:  # a name button under the form's answer
        await interaction.response.defer()
        drivers = await fetch_safety()
        name = known(drivers, cid[len(PICK):]) if drivers else None
        text = (await link_member(interaction, name, drivers) if name
                else "That driver isn't on the site anymore. Pick a letter again.")
        await interaction.edit_original_response(content=text, view=None)
        return
    if cid == GROUP:  # a letter: answer privately with the names in it
        await interaction.response.defer(ephemeral=True, thinking=True)
        drivers = await fetch_safety()
        group = dict(letter_groups(drivers or {})).get((values or [""])[0])
        if not group:
            await interaction.followup.send("That letter has no drivers anymore. Pick again.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Drivers starting with **{values[0]}**. Pick your name (type to filter):",
            view=names_view(group, linked_to(interaction.guild)), ephemeral=True)
        return
    private = bool(getattr(getattr(interaction.message, "flags", None), "ephemeral", False))
    if (cid.startswith(NAMES) or cid.startswith(PICK)) and values and private:  # a name in the private answer
        await interaction.response.defer()
        drivers = await fetch_safety()
        name = known(drivers, values[0]) if drivers else None
        text = (await link_member(interaction, name, drivers) if name
                else "That driver isn't on the site anymore. Pick a letter again.")
        await interaction.edit_original_response(content=text, view=None)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    if cid == UNLINK:
        done = store.unlink(interaction.user.id)
        if done and interaction.guild:
            try:
                await set_roles(interaction.guild, interaction.user.id, None)
            except discord.DiscordException as e:
                log.warning("SR role not removed: %s", e)
        await interaction.followup.send("Unlinked." if done else "You weren't linked to a driver.", ephemeral=True)
        return
    # a name dropdown on an old panel, until the panel is edited
    drivers = await fetch_safety()
    name = known(drivers, (values or [""])[0]) if drivers else None
    if not name:
        await interaction.followup.send("That driver isn't in the list anymore. Try again.", ephemeral=True)
        return
    await interaction.followup.send(await link_member(interaction, name, drivers), ephemeral=True)


async def sync(guild):
    drivers = await fetch_safety()
    if not drivers:
        return
    members = [(m.id, names_of(m)) for m in guild.members if not m.bot]
    new = store.auto(members, list(drivers))
    for uid, d in new:
        log.info("linked %s to %s by name", uid, d)
    try:
        await keep_panel(guild, drivers)
    except discord.DiscordException as e:
        log.warning("link panel not posted: %s", e)
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
        names = sorted((n for n in drivers if cur in n.lower() and not L.HIDDEN.search(n)),
                       key=lambda n: (not n.lower().startswith(cur), n.lower()))
        names += [n for n in L.suggest(current, list(drivers), 25) if n not in names]  # typos too
        return [app_commands.Choice(name=n, value=n) for n in names[:25]]

    @tree.command(name="link", description="Link your Discord to your driver name for the safety rating", guild=server)
    @app_commands.describe(driver="Your name in the game, as it shows on the race stats site")
    @app_commands.autocomplete(driver=driver_choices)
    async def link(interaction: discord.Interaction, driver: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        drivers = await fetch_safety()
        name = next((n for n in drivers if n.lower() == driver.strip().lower()), None)
        if not name:
            close = L.suggest(driver, list(drivers))
            name = close[0] if len(close) == 1 and L.is_name(driver, close[0]) else None
        if not name:
            shown = discord.utils.escape_markdown(driver)
            await interaction.followup.send(
                (f"No driver is called exactly **{shown}**. Which one are you?" if close else
                 f"I don't know the driver **{shown}** yet. You're in the list once you've raced with us "
                 "since 7 Oct 2026."), view=choices_view(close, interaction.user.id), ephemeral=True)
            return
        await interaction.followup.send(await link_member(interaction, name, drivers), ephemeral=True)

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

    @bot.event
    async def on_interaction(interaction):
        try:
            await on_panel(interaction)
        except Exception as e:  # never let a click take the bot down
            log.warning("link panel click failed: %s", e)
        for handler in CLICKS:  # other panels (the giveaway)
            try:
                await handler(interaction)
            except Exception as e:
                log.warning("click failed: %s", e)


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
