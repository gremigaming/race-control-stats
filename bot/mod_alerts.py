"""Alerts for the mods in MOD-ALERTS (GreMi 2026-10-14).

Race Control posts there when:
- a driver crosses a safety rating kick line (20+ lost in a race, 40+ in a stream);
- a driver's SR goes below 0 (banned);
- a driver protests a penalty, or a hidden car was merged into a named driver;
- a new session arrives (the uploader sends every save right after it ends) with
  a driver who was kicked before (and how often), a banned driver (must leave),
  or a car without a name whose race number + nationality + platform matches a
  kicked or banned driver (they may have hidden their name to slip back in).

Sources: the race-stats site (league/safety.json, league/merges.json), the raw
race files and races/corrections.json in the public race-stats repo, and the
protests the staff page keeps on this server. Which cars a driver drove is
worked out here and kept on this server only (STATE), never published.

The checks at the top have no Discord code, so they can be tested on their own.
"""
import asyncio
import json
import logging
import os
import pathlib
import re
import time
import urllib.parse

CHANNEL_ID = 1559833714819727372  # 🚨┃MOD-ALERTS, staff only, Race Control posts
STAFF_URL = os.environ.get("STAFF_URL", "https://104-154-182-45.sslip.io").rstrip("/")
SITE = "https://gremigaming.github.io/race-stats/"
RAW = "https://raw.githubusercontent.com/gremigaming/race-stats/main/"
API = "https://api.github.com/repos/gremigaming/race-stats/"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_MOD_ALERTS", "/var/lib/race-control/mod_alerts.json"))
PROTESTS_FILE = pathlib.Path(os.environ.get("STAFF_PROTESTS_FILE", "/var/lib/race-control/protests.json"))
EVERY = 60          # seconds between checks
SITE_EVERY = 300    # seconds between site checks (SR, merges)
CAR_SESSIONS = 8    # newest sessions of a watched driver read to learn their cars
log = logging.getLogger("race_control.mod_alerts")


# ---------------------------------------------------------------- checks

def norm(name):
    return re.sub(r"[\s ]+", " ", name or "").strip().lower()


def cars_of(data):
    """The human cars in a raw Pits n' Giggles save."""
    out = []
    for d in data.get("classification-data") or []:
        p = d.get("participant-data") or {}
        if p.get("ai-controlled"):
            continue
        number, nat = p.get("race-number"), p.get("nationality") or "Unspecified"
        default = number == 2 and nat == "Unspecified"
        out.append({"index": d.get("index"), "name": d.get("driver-name") or "",
                    "hidden": p.get("show-online-names") is False,
                    "combo": None if default else f"{number}|{nat}|{p.get('platform')}",
                    "car": f"{p.get('team-id', '?')} #{number}, {nat}, {p.get('platform')}"})
    return out


def kick_counts(changes):
    """{driver lower: (shown name, kicks, last day)} from the staff kicks."""
    out = {}
    for c in changes:
        if c.get("type") != "kick":
            continue
        name = re.sub(r"\s*\(hidden as .*\)$", "", c.get("driver") or "").strip()
        if not name:
            continue
        _, n, last = out.get(norm(name), (name, 0, ""))
        out[norm(name)] = (name, n + 1, max(last, c.get("day", "")))
    return out


def session_alerts(file, cars, kicks, banned, combos):
    """(key, text) per alert for one new session.
    kicks: kick_counts(); banned: {lower: shown}; combos: {lower: [combo]} of watched drivers."""
    m = re.search(r"(\d{4}_\d{2}_\d{2})_\d{2}_\d{2}_\d{2}\.json$", file)
    day = m.group(1) if m else file
    where = session_label(file)
    named = {norm(c["name"]) for c in cars if not c["hidden"]}
    out = []
    for c in cars:
        key = norm(c["name"])
        if c["hidden"]:
            continue
        if key in banned:
            out.append((f"banned|{day}|{key}", f"🚫 **{c['name']}** is banned (SR below 0) and is in the lobby ({where}). They have to leave."))
        elif key in kicks:
            name, n, last = kicks[key]
            times = "once" if n == 1 else f"{n} times"
            out.append((f"kicked|{day}|{key}", f"⚠️ **{c['name']}** is in the lobby ({where}). Kicked {times} before, last on {last}."))
    for c in cars:
        if not c["hidden"] or not c["combo"]:
            continue
        for who, theirs in combos.items():
            if who in named or c["combo"] not in theirs:
                continue
            shown = banned.get(who) or (kicks.get(who) or (who,))[0]
            why = "banned" if who in banned else "kicked before"
            out.append((f"hidden|{day}|{c['combo']}|{who}",
                        f"🕵️ A car without a name ({c['car']}) in {where} matches **{shown}** ({why}). "
                        "Same race number, nationality and platform: they may have hidden their name."))
    return out


def session_label(file):
    m = re.match(r"(.+?)_(?:Just_in_case_|Manual_)?(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_\d{2}\.json$", file)
    if not m:
        return file
    kind, rest = ("Quali", m.group(1).split("Qualifying_", 1)[-1]) if "Qualifying" in m.group(1) else \
        ("Race", m.group(1).split("Race_", 1)[-1])
    return f"{kind} {rest.replace('_', ' ')}, {m.group(5)}:{m.group(6)}"


def safety_alerts(safety):
    """(key, text) for kick lines crossed and banned drivers."""
    out = []
    for d in safety.get("drivers", []):
        for f in d.get("flags", []):
            if "kick line" in f:
                out.append((f"flag|{d['name']}|{f}", f"🛑 **{d['name']}** should be kicked: {f}."))
        if d.get("banned"):
            out.append((f"ban|{norm(d['name'])}", f"🚫 **{d['name']}** is now below 0 SR ({d.get('sr')}) and is banned."))
    return out


def protest_alerts(protests):
    out = []
    for p in protests:
        if p.get("status") == "open":
            what = (p.get("text") or "").strip().replace("\n", " ")
            what = what[:200] + ("…" if len(what) > 200 else "")
            who = p.get("linked") or p.get("by_name") or "?"
            out.append((f"protest|{p.get('id')}", f"📝 New protest from **{who}** about: {p.get('what', '')}\n> {what}\n"
                                                   f"Answer it on the staff page: <{STAFF_URL}/protests>"))
    return out


def merge_alerts(merges):
    out = []
    for m in merges.get("merges", []):
        if not m.get("off"):
            hidden = ", ".join(m.get("hidden", []))
            out.append((f"merge|{m['id']}", f"🔗 Hidden car {hidden} ({m.get('car')}, {m.get('day')}) is now counted as **{m['name']}**. "
                                            f"Wrong? Split it under Names: <{STAFF_URL}/names>"))
    return out


def pending(found, sent):
    seen, out = set(sent), []
    for k, t in found:
        if k not in seen:
            seen.add(k)
            out.append((k, t))
    return out


# ---------------------------------------------------------------- running

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


def raw_path(file):
    m = re.search(r"(\d{4})_(\d{2})_(\d{2})_\d{2}_\d{2}_\d{2}\.json$", file)
    return f"races/{m.group(1)}_{m.group(2)}_{m.group(3)}/{file}" if m else None


class Feeds:
    def __init__(self, session):
        self.session = session
        token = os.environ.get("STAFF_GITHUB_TOKEN", "")
        self.gh = {"Accept": "application/vnd.github+json"}
        if token:
            self.gh["Authorization"] = f"Bearer {token}"
        self.etag = None

    async def json(self, url, headers=None):
        async with self.session.get(url, headers=headers or {}) as r:
            if r.status != 200:
                return None
            return json.loads(await r.text())

    async def site(self, path):
        return await self.json(f"{SITE}{path}?t={int(time.time())}") or {}

    async def raw(self, path):
        return await self.json(RAW + urllib.parse.quote(path))

    async def new_race_files(self, last_sha):
        """([raw paths of race files added since last_sha], newest sha)."""
        headers = dict(self.gh)
        if self.etag:
            headers["If-None-Match"] = self.etag
        async with self.session.get(API + "commits?path=races&per_page=10", headers=headers) as r:
            if r.status != 200:
                return [], last_sha
            self.etag = r.headers.get("ETag")
            commits = await r.json()
        if not commits:
            return [], last_sha
        newest = commits[0]["sha"]
        if last_sha is None or newest == last_sha:
            return [], newest
        shas = [c["sha"] for c in commits]
        newer = shas[:shas.index(last_sha)] if last_sha in shas else shas
        paths = []
        for sha in reversed(newer):
            detail = await self.json(API + "commits/" + sha, self.gh) or {}
            paths += [f["filename"] for f in detail.get("files", [])
                      if f.get("status") == "added" and f["filename"].endswith(".json")
                      and not f["filename"].endswith(("aliases.json", "corrections.json"))]
        return paths, newest


async def learn_cars(feeds, state, safety, watched):
    """The cars (combos) each watched driver drove, from their newest raw sessions."""
    combos = state.setdefault("combos", {})
    sessions = {norm(d["name"]): [s["file"] for s in d.get("sessions", [])] for d in safety.get("drivers", [])}
    for who in watched:
        files = sessions.get(who, [])[:CAR_SESSIONS]
        known = combos.get(who, {"files": [], "combos": []})
        todo = [f for f in files if f not in known["files"]]
        for f in todo:
            path = raw_path(f)
            data = await feeds.raw(path) if path else None
            for c in cars_of(data or {}):
                if not c["hidden"] and norm(c["name"]) == who and c["combo"] and c["combo"] not in known["combos"]:
                    known["combos"].append(c["combo"])
            known["files"].append(f)
        combos[who] = known
    return {w: combos[w]["combos"] for w in watched if combos.get(w, {}).get("combos")}


async def kicked_cars(feeds, state, changes):
    """Combos of the cars staff kicked (also hidden ones), by kicked driver."""
    seen = state.setdefault("kick_cars", {})
    out = {}
    for c in changes:
        if c.get("type") != "kick":
            continue
        who = norm(re.sub(r"\s*\(hidden as .*\)$", "", c.get("driver") or ""))
        for k in c.get("cars", []):
            ref = f"{k.get('file')}|{k.get('car')}"
            if ref not in seen:
                path = raw_path(k.get("file", ""))
                data = await feeds.raw(path) if path else None
                car = next((x for x in cars_of(data or {}) if x["index"] == k.get("car")), None)
                seen[ref] = car["combo"] if car else None
            if seen[ref]:
                out.setdefault(who, set()).add(seen[ref])
    return out


async def run(bot):
    """Runs next to the Discord bot; never raises."""
    import aiohttp
    import discord
    state = read_state()
    first = "sent" not in state
    sent = list(state.get("sent", []))
    safety, merges, checked_site = {}, {}, 0.0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        feeds = Feeds(session)
        channel = None
        while True:
            try:
                if channel is None:
                    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
                    if not state.get("pinned"):
                        msg = await channel.send(
                            f"🚨 **Mod alerts.** Race Control posts here when someone has to be kicked, a banned or "
                            f"kicked driver is in the lobby, a car without a name looks like one of them, or a "
                            f"driver protests a penalty. Alerts come right after each session is uploaded.\n"
                            f"🛠️ Staff page (log in with Discord): {STAFF_URL}",
                            allowed_mentions=discord.AllowedMentions.none())
                        try:
                            await msg.pin()
                        except Exception as e:
                            log.warning("mod alerts pin: %s", e)
                        state["pinned"] = msg.id
                now = time.time()
                found = []
                if now - checked_site >= SITE_EVERY:
                    safety = await feeds.site("league/safety.json") or safety
                    merges = await feeds.site("league/merges.json") or merges
                    checked_site = now
                found += safety_alerts(safety) + merge_alerts(merges)
                try:
                    found += protest_alerts(json.loads(PROTESTS_FILE.read_text()))
                except (OSError, ValueError):
                    pass

                paths, state["last_sha"] = await feeds.new_race_files(state.get("last_sha"))
                if paths:
                    changes = (await feeds.raw("races/corrections.json") or {}).get("changes", [])
                    kicks = kick_counts(changes)
                    banned = {norm(d["name"]): d["name"] for d in safety.get("drivers", []) if d.get("banned")}
                    combos = await learn_cars(feeds, state, safety, set(kicks) | set(banned))
                    for who, cs in (await kicked_cars(feeds, state, changes)).items():
                        combos[who] = sorted(set(combos.get(who, [])) | cs)
                    for path in paths:
                        data = await feeds.raw(path)
                        if data:
                            found += session_alerts(path.rsplit("/", 1)[-1], cars_of(data), kicks, banned, combos)

                new = pending(found, sent)
                if first:  # don't flood the channel with everything from before
                    sent += [k for k, _ in new]
                    first = False
                    new = [(k, t) for k, t in new if k.startswith(("banned|", "kicked|", "hidden|"))]
                for key, text in new:
                    await channel.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
                    sent.append(key)
                sent = sent[-2000:]
                state["sent"] = sent
                save_state(state)
            except Exception as e:
                log.warning("mod alerts: %s", e)
            await asyncio.sleep(EVERY)
