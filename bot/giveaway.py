"""GreMi's weekly giveaway (2026-10-13): a €10 gift card, drawn once there
have been 5 new Twitch subs.

How a round goes:
  open     members with the Prize Hunter role press Enter on the panel in
           GIVEAWAYS. New, Prime and gifted subs count (resubs don't); they
           come in from Twitch through bot/twitch_chat.py (GreMi's own login).
  drawing  the 5th new sub sets the draw 10 minutes later. Race Control says
           so in Twitch (and YouTube) chat with the Discord link, so new people
           can still join, and in GIVEAWAYS.
  closed   the draw: entries come from each member's roles at that moment
           (bot/giveaway.json), so they can go up or down while entered.
           The next round opens at midnight (Amsterdam) the day after.

The OBS browser source (subs left until the draw) is served by the staff page at
https://<server>.sslip.io/giveaway; the address is in the bot's journal log.
State lives on the server only (STATE), never in this public repo.
The decisions at the top have no Discord code, so they can be tested on their own.
"""
import asyncio
import datetime as dt
import json
import logging
import os
import pathlib
import random
import time
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = HERE / "giveaway.json"
OVERLAY = HERE / "giveaway_overlay.html"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_GIVEAWAY", "/var/lib/race-control/giveaway.json"))
TZ = ZoneInfo("Europe/Amsterdam")
TICK = 5          # seconds between checks
PANEL_EVERY = 15  # seconds between panel edits at most
log = logging.getLogger("race_control.giveaway")


# ---------------------------------------------------------------- decisions

def load_config(path=CONFIG):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def fresh(round_no, now):
    return {"round": round_no, "status": "open", "opened": now, "subs": [], "entrants": [],
            "draw_at": None, "reopen_at": None}


def add_sub(state, sub, now, config):
    """Counts one new sub. Returns "draw_soon" when it was the last one needed,
    "count" when it counted, None when no round is open."""
    if state["status"] != "open":
        return None
    state["subs"].append(dict(sub, at=now))
    if len(state["subs"]) >= config["need"]:
        state["status"] = "drawing"
        state["draw_at"] = now + config["delay_minutes"] * 60
        return "draw_soon"
    return "count"


def reopen_time(drawn_at):
    """Midnight (Amsterdam) at the start of the day after the draw."""
    day = dt.datetime.fromtimestamp(drawn_at, TZ).date() + dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(0, 0), TZ).timestamp()


def due(state, now):
    if state["status"] == "drawing" and now >= state["draw_at"]:
        return "draw"
    if state["status"] == "closed" and now >= (state.get("reopen_at") or 0):
        return "reopen"
    return None


def entries(role_ids, config):
    """(total, [(label, extra entries)]) for a member with these role ids."""
    role_ids = {int(r) for r in role_ids}
    extra = [(b["label"], b["entries"]) for b in config["bonus"] if b["role"] in role_ids]
    return config["base"] + sum(n for _, n in extra), extra


def pick(weights, rng=random.SystemRandom()):
    """A key of weights {key: entries}, each entry one ticket; None when empty."""
    pool = [(k, n) for k, n in weights.items() if n > 0]
    if not pool:
        return None
    ticket = rng.randrange(sum(n for _, n in pool))
    for key, n in pool:
        if ticket < n:
            return key
        ticket -= n


def subs_left(state, config):
    return max(0, config["need"] - len(state["subs"]))


def status_line(state, now, config):
    """The changing part of the Twitch chat timer."""
    if state["status"] == "open":
        left = subs_left(state, config)
        return f"Only {left} more new sub{'' if left == 1 else 's'} until the draw!"
    if state["status"] == "drawing":
        mins = max(1, round((state["draw_at"] - now) / 60))
        return f"The draw is in {mins} minute{'' if mins == 1 else 's'}!"
    return "The next one opens tomorrow."


def public(state, config, now):
    """What the OBS counter shows: no member names except the last winner's
    display name (also said in chat)."""
    last = state.get("last") or {}
    return {"status": state["status"], "round": state["round"], "need": config["need"],
            "subs": min(len(state["subs"]), config["need"]), "left": subs_left(state, config),
            "draw_at": state.get("draw_at"), "reopen_at": state.get("reopen_at"),
            "delay": config["delay_minutes"] * 60, "entered": len(state["entrants"]),
            "winner": last.get("name"), "won_at": last.get("at"), "now": now}


def entries_text(config):
    """The entries table on the panel. Role mentions show in the role's colour
    (the panel pings nobody)."""
    role = {b["label"]: (b["role"], b["entries"]) for b in config["bonus"]}

    def one(label):
        rid, n = role[label]
        return f"<@&{rid}> **+{n}**"

    def row(labels):
        return " · ".join(one(k) for k in labels if k in role)

    awards = [k for k in ("Most Wins", "Most FL", "Most DOTD", "Most Overtakes", "Cleanest Driver") if k in role]
    lines = [f"🏁 Everyone with <@&{config['entry_role']}> starts with **{config['base']} entries**", "",
             "💜 **Supporters**", row(["Twitch sub Tier 1", "Twitch sub Tier 2", "Twitch sub Tier 3"]),
             row(["Server Booster", "Simgasm Buyer"]), "",
             "⭐ **Community**", row(["VIP", "OG"]), ""]
    if awards:
        lines += [f"🏆 **Award roles** (+{role[awards[0]][1]} each)", " ".join(f"<@&{role[k][0]}>" for k in awards), ""]
    lines += ["🛡️ **Safety rating**", row(["SR S+", "SR S", "SR A", "SR B", "SR C"]),
              "-# SR D, E and F give no extra entries."]
    return "\n".join(lines)


def entrants_text(entered, limit=40):
    """entered: [(uid, entries)] -> who's in, most entries first."""
    if not entered:
        return "Nobody yet. Be the first!"
    rows = sorted(entered, key=lambda x: -x[1])
    out = " · ".join(f"<@{uid}> 🎟️{n}" for uid, n in rows[:limit])
    if len(rows) > limit:
        out += f" · and {len(rows) - limit} more"
    return out


# ---------------------------------------------------------------- state on disk

def read_state(now=None):
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return fresh(1, now or time.time())


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


# ---------------------------------------------------------------- Discord

ENTER, MINE, LEAVE = "giveaway:enter", "giveaway:mine", "giveaway:leave"
STATE_NOW = None   # the running state, for the Twitch chat timer and the OBS counter
ANNOUNCE = None    # async fn(text): Twitch (and YouTube) chat, set by bot/twitch_chat.py
_wake = asyncio.Event()


def chat_fill(text):
    """Fills {giveaway} in a Twitch chat timer."""
    if "{giveaway}" not in text:
        return text
    line = status_line(STATE_NOW, time.time(), load_config()) if STATE_NOW else ""
    return text.replace("{giveaway}", line).replace("  ", " ")


async def on_sub(sub):
    """A new sub from Twitch: {"user": login, "gift": bool}."""
    state, config = STATE_NOW, load_config()
    if state is None:
        return
    what = add_sub(state, sub, time.time(), config)
    if what is None:
        return
    save_state(state)
    log.info("giveaway: sub %d/%d", len(state["subs"]), config["need"])
    _wake.set()


def panel(state, config, now, entered=None):
    """The giveaway panel (Components V2 JSON). entered: [(uid, entries now)]."""
    status = state["status"]
    need, have = config["need"], min(len(state["subs"]), config["need"])
    if entered is None:
        entered = [(uid, config["base"]) for uid in state["entrants"]]
    if status == "open":
        dots = "🟣" * have + "⚫" * (need - have)
        head = (f"## {dots}  {have} / {need} new subs\n"
                f"⏱️ The draw starts **{config['delay_minutes']} minutes after the {need}th new Twitch sub** "
                "since this round opened. New, Prime and gifted subs count.")
    elif status == "drawing":
        head = (f"## 🥁 Draw <t:{int(state['draw_at'])}:R>\n"
                f"{need} new subs reached! The winner is drawn at <t:{int(state['draw_at'])}:t>. "
                "There's still time to enter.")
    else:
        last = state.get("last") or {}
        who = f"<@{last['uid']}>" if last.get("uid") else "nobody (no entries)"
        head = (f"## 🏆 Round {state['round']} winner: {who}\n"
                f"The next giveaway opens <t:{int(state['reopen_at'])}:R>.")
    total = sum(n for _, n in entered)
    body = [
        {"type": 10, "content": f"-# WEEKLY GIVEAWAY · ROUND {state['round']}\n"
                                f"# 🎁 {config['prize']}\n🎮 {config['prize_choice']}."},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 10, "content": head},
        {"type": 14, "divider": True, "spacing": 2},
        {"type": 10, "content": "## 🎟️ How many entries do you get?\n" + entries_text(config)},
        {"type": 14, "divider": True, "spacing": 2},
        {"type": 10, "content": f"## 👥 Entered: {len(entered)}" + (f" · {total} entries" if entered else "")
                                + "\n" + entrants_text(entered)
                                + "\n-# Entries are counted again at the draw from your roles at that moment, "
                                  "so they go up or down with your roles. Drop an SR rank and you lose "
                                  "those extra entries."},
        {"type": 1, "components": [
            {"type": 2, "style": 3, "label": "Enter", "emoji": {"name": "🎟️"}, "custom_id": ENTER,
             "disabled": status == "closed"},
            {"type": 2, "style": 2, "label": "My entries", "emoji": {"name": "📊"}, "custom_id": MINE},
            {"type": 2, "style": 2, "label": "Leave", "custom_id": LEAVE, "disabled": status == "closed"},
        ]},
    ]
    return {"flags": 1 << 15, "allowed_mentions": {"parse": []},
            "components": [{"type": 17, "accent_color": 0x9146FF, "components": body}]}


class Runner:
    def __init__(self, bot, guild_id, owner_id, logs_channel_id):
        self.bot, self.guild_id, self.owner_id, self.logs = bot, guild_id, owner_id, logs_channel_id
        self.shown, self.edited = None, 0.0

    async def api(self, method, path, **kw):
        from discord.http import Route
        return await self.bot.http.request(Route(method, path, **kw.pop("params", {})), **kw)

    async def post(self, channel_id, text, users=()):
        await self.api("POST", "/channels/{channel_id}/messages", params={"channel_id": channel_id},
                       json={"content": text, "allowed_mentions": {"parse": [], "users": [str(u) for u in users]}})

    async def keep_panel(self, state, config, now, force=False):
        body = panel(state, config, now, self.entered(state, config))
        if body == self.shown and not force:
            return
        if not force and now - self.edited < PANEL_EVERY:
            return
        self.edited = now
        ch = {"channel_id": config["channel"]}
        if state.get("panel"):
            try:
                await self.api("PATCH", "/channels/{channel_id}/messages/{message_id}",
                               params=dict(ch, message_id=state["panel"]), json=body)
                self.shown = body
                return
            except Exception as e:  # deleted or never there: post a new one
                log.warning("giveaway panel edit failed, posting a new one: %s", e)
        msg = await self.api("POST", "/channels/{channel_id}/messages", params=ch, json=body)
        state["panel"] = msg["id"]
        save_state(state)
        self.shown = body

    def entered(self, state, config):
        """[(uid, entries right now)] of the members who entered."""
        guild = self.bot.get_guild(self.guild_id)
        out = []
        for uid in state["entrants"]:
            m = guild.get_member(int(uid)) if guild else None
            if m is not None:
                out.append((uid, entries([r.id for r in m.roles], config)[0]))
        return out

    async def say(self, text):
        if ANNOUNCE:
            try:
                await ANNOUNCE(text)
            except Exception as e:
                log.warning("giveaway chat message failed: %s", e)

    async def draw(self, state, config, now):
        guild = self.bot.get_guild(self.guild_id)
        weights, members = {}, {}
        for uid in state["entrants"]:
            m = guild.get_member(int(uid)) if guild else None
            if (m is None or m.bot or m.id == self.owner_id
                    or config["entry_role"] not in {r.id for r in m.roles}):
                continue
            weights[uid] = entries([r.id for r in m.roles], config)[0]
            members[uid] = m
        winner = pick(weights)
        total = sum(weights.values())
        state.update(status="closed", reopen_at=reopen_time(now),
                     last={"uid": winner, "entries": weights.get(winner, 0), "total": total,
                           "drivers": len(weights), "at": now,
                           "name": members[winner].display_name if winner else None})
        state.setdefault("history", []).append(dict(state["last"], round=state["round"]))
        save_state(state)
        ch = config["channel"]
        if winner is None:
            await self.post(ch, f"🎁 Round {state['round']}: nobody entered, so no winner this time. "
                                f"The next giveaway opens <t:{int(state['reopen_at'])}:R>.")
            await self.say("🎁 Nobody entered the giveaway this time. The next one opens tomorrow: "
                           + config["invite"])
            return
        m = members[winner]
        await self.post(ch, f"🏆 Congratulations <@{winner}>! You won the **{config['prize']}** "
                            f"(round {state['round']}) with {weights[winner]} of {total} entries from "
                            f"{len(weights)} drivers.\nSend GreMi a DM with the card you want: "
                            f"{config['prize_choice'].split(', the winner')[0]}.\n"
                            f"-# The next giveaway opens <t:{int(state['reopen_at'])}:R>.", users=[winner])
        await self.say(f"🏆 {m.display_name} won the weekly giveaway ({config['prize']})! "
                       f"Congrats! The next one opens tomorrow on the Discord: {config['invite']}")
        try:
            role = guild.get_role(config["winner_role"])
            if role and role not in m.roles:
                await m.add_roles(role, reason="Won the weekly giveaway")
        except Exception as e:
            log.warning("winner role not given: %s", e)
        try:
            await self.post(self.logs, f"🎁 Giveaway round {state['round']} won by <@{winner}> "
                                       f"({weights[winner]}/{total} entries). GreMi sends the gift card.")
        except Exception as e:
            log.warning("giveaway log failed: %s", e)

    async def step(self):
        global STATE_NOW
        state, config, now = STATE_NOW, load_config(), time.time()
        what = due(state, now)
        if what == "draw":
            await self.draw(state, config, now)
        elif what == "reopen":
            STATE_NOW = state = dict(fresh(state["round"] + 1, now), panel=state.get("panel"),
                                     history=state.get("history", []))
            save_state(state)
            log.info("giveaway round %d open", state["round"])
        if state["status"] == "drawing" and not state.get("announced"):
            state["announced"] = True
            save_state(state)
            await self.say(f"🎁 That's {config['need']} new subs! The weekly giveaway ({config['prize']}) "
                           f"is drawn in {config['delay_minutes']} minutes. Join the Discord, get the "
                           f"Prize Hunter role and press Enter in #giveaways: {config['invite']}")
            await self.post(config["channel"], f"🥁 {config['need']} new subs reached! The draw is "
                                               f"<t:{int(state['draw_at'])}:R>. Not entered yet? "
                                               "Press Enter on the panel above.")
        await self.keep_panel(state, config, now, force=bool(what))

    async def run(self):
        global STATE_NOW
        STATE_NOW = read_state()
        save_state(STATE_NOW)
        await self.bot.wait_until_ready()
        url = await overlay_url()
        if url:
            log.info("OBS giveaway counter: %s", url)
        while True:
            try:
                await self.step()
            except Exception as e:  # never let the giveaway take the bot down
                log.warning("giveaway step failed: %s", e)
            _wake.clear()
            try:
                await asyncio.wait_for(_wake.wait(), TICK)
            except asyncio.TimeoutError:
                pass

    async def report(self, text):
        try:
            await self.post(self.logs, f"🎁 {text}")
        except Exception as e:
            log.warning("giveaway report failed: %s", e)

    async def on_click(self, interaction):
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("giveaway:"):
            return
        state, config = STATE_NOW, load_config()
        user = interaction.user
        roles = [r.id for r in getattr(user, "roles", [])]
        total, extra = entries(roles, config)
        uid = str(user.id)
        breakdown = f"Base **{config['base']}**" + "".join(f" · {lbl} **+{n}**" for lbl, n in extra)
        if user.id == self.owner_id:
            text = "It's your own giveaway, so you can't enter. 😄"
        elif cid == ENTER:
            if config["entry_role"] not in roles:
                text = ("You need the **Prize Hunter** role to enter. Add it in Channels & Roles "
                        "(Browse Channels at the top of the channel list), then press Enter again.")
            elif state["status"] == "closed":
                text = "This round is over. The next one opens at midnight."
            else:
                if uid not in state["entrants"]:
                    state["entrants"].append(uid)
                    save_state(state)
                    _wake.set()
                text = (f"🎟️ You're in with **{total} entries** right now.\n{breakdown}\n"
                        "-# Counted again at the draw from your roles at that moment.")
        elif cid == LEAVE:
            if uid in state["entrants"]:
                state["entrants"].remove(uid)
                save_state(state)
                _wake.set()
                text = "You left this giveaway. Press Enter to join again."
            else:
                text = "You weren't entered."
        else:
            inside = uid in state["entrants"]
            text = (f"{'🎟️ You are entered' if inside else 'You are not entered yet'}. "
                    f"Your roles give **{total} entries** right now.\n{breakdown}")
        await interaction.response.send_message(text, ephemeral=True)


# ---------------------------------------------------------------- OBS counter
# The staff page (staff/app.py, behind Caddy with HTTPS) serves /giveaway and
# /giveaway.json from STATE and OVERLAY, so OBS needs no extra open port.

async def overlay_url():
    ip = await public_ip()
    return f"https://{ip.replace('.', '-')}.sslip.io/giveaway" if ip else None


async def public_ip():
    """The server's own address, from Google Cloud's metadata server."""
    import aiohttp
    url = ("http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/"
           "access-configs/0/external-ip")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get(url, headers={"Metadata-Flavor": "Google"}) as r:
                return (await r.text()).strip() if r.status == 200 else None
    except Exception:
        return None
