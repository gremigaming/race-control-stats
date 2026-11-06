"""Staff page for the GreMi Gang race stats site.

Moderators log in with Discord and fix what the automatic data gets wrong:
safety rating incidents, results and driver names, and kick a driver from a
stream. Every change is one entry in races/corrections.json in
gremigaming/race-stats; the site applies it the next time it builds (about 2
minutes) and any entry can be undone.

Drivers protest an incident right on the site: the form there posts to
/api/protest (no login, they give their name), or here at /protest with a
Discord login. Protests are kept on this server only (PROTESTS_FILE) and show
up for the staff under Protests.

Runs behind Caddy (HTTPS) on the Race Control server, standard library only.
Needs in the environment: DISCORD_CLIENT_SECRET, DISCORD_BOT_TOKEN,
STAFF_GITHUB_TOKEN, STAFF_SECRET and STAFF_URL (the public https address).
Run with: python3 -m staff.app
"""
import base64
import copy
import datetime
import hashlib
import hmac
import html
import json
import os
import pathlib
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from staff import calls
from staff import races

CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "1532679389429502012")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GITHUB_TOKEN = os.environ.get("STAFF_GITHUB_TOKEN", "")
SECRET = os.environ.get("STAFF_SECRET", "").encode() or secrets.token_bytes(32)
BASE_URL = os.environ.get("STAFF_URL", "http://localhost:8090").rstrip("/")
PORT = int(os.environ.get("STAFF_PORT", "8090"))
GUILD_ID = 1079917337165172876  # the real GreMi_Gaming server
STAFF_ROLES = {"moderator", "admin", "owner"}
REPO = "gremigaming/race-stats"
SITE = "https://gremigaming.github.io/race-stats/"
STAFF_FILE = pathlib.Path(__file__).resolve().parents[1] / "bot" / "staff.json"
NAMES_FILE = pathlib.Path(os.environ.get("STAFF_NAMES_FILE", "/var/lib/race-control/staff-names.json"))
PROTESTS_FILE = pathlib.Path(os.environ.get("STAFF_PROTESTS_FILE", "/var/lib/race-control/protests.json"))
CLIPS_DIR = pathlib.Path(os.environ.get("STAFF_CLIPS_DIR", "/var/lib/race-control/clips"))
CLIP_MAX = 100 * 1024 * 1024  # one clip
CLIPS_TOTAL = 3 * 1024 ** 3   # all clips together, so the disk can't fill up
CLIP_TYPES = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/x-matroska": "mkv"}
UA = "DiscordBot (https://github.com/gremigaming/race-control-stats, 1.0)"
SESSION_DAYS = 14
AI_NAME_RE = re.compile(r" #\d+$")  # AI and hidden names look like "Ferrari '26 #21"


class _AiName:
    """AI_NAME.search(name): an AI car or hidden name, but not a real driver whose name ends in "#13"
    (league/names.json lists every shown name, from the game's own flags)."""
    @staticmethod
    def search(n):
        if not AI_NAME_RE.search(n or ""):
            return False
        try:
            return n not in set(site_json("league/names.json", ttl=600).get("names", []))
        except Exception:
            return True


AI_NAME = _AiName()

# ---------------------------------------------------------------- outside calls


def http(url, data=None, headers=None, method=None):
    """JSON in, JSON out. Raises urllib.error.HTTPError on 4xx/5xx."""
    body = None
    h = {"User-Agent": UA, **(headers or {})}
    if isinstance(data, dict) and h.get("Content-Type") == "application/x-www-form-urlencoded":
        body = urllib.parse.urlencode(data).encode()
    elif data is not None:
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


_cache = {}
_cache_lock = threading.Lock()


LIVE_LEAGUE = "https://replays.gremigaming.com/league/"


def live_url(path):
    """Where the site reads this league file right now: the live server (R2) when it has it,
    else None (GitHub Pages). The website reads the live server first, so the staff page
    does too: both then show the same SR points."""
    if not path.startswith("league/"):
        return None
    now = time.time()
    with _cache_lock:
        hit = _cache.get("#live-index")
    if not hit or now - hit[0] > 30:
        try:
            idx = http(LIVE_LEAGUE + f"index.json?t={int(now)}")
            if not (idx.get("files") or idx.get("generation")):
                idx = {}
        except Exception:
            idx = {}
        hit = (now, idx)
        with _cache_lock:
            _cache["#live-index"] = hit
    mapped = (hit[1].get("assetPaths") or {}).get(urllib.parse.unquote(path[len("league/"):]))
    if not mapped:
        return None
    return mapped if mapped.startswith("http") else LIVE_LEAGUE + urllib.parse.quote(mapped.lstrip("/"), safe="/")


def site_json(path, ttl=60):
    """A league file as the website shows it (live server first, then the Pages build), cached for ttl seconds."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
    data = None
    url = live_url(path)
    if url:
        try:
            data = http(url)
        except Exception:
            data = None
    if data is None:
        data = http(SITE + path + f"?t={int(now)}")
    with _cache_lock:
        if len(_cache) > 60:
            _cache.clear()
        _cache[path] = (now, data)
    return data


GH = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def gh_call(url, data=None, method=None, tries=4):
    """A GitHub API call that tries again when GitHub has a hiccup (5xx, timeouts).
    GitHub answers 500/502/503 now and then, mostly when several commits land on
    main at the same moment (the site build, the grading sync, a staff save)."""
    for n in range(tries):
        try:
            return http(url, data, headers={**GH, "Authorization": f"Bearer {GITHUB_TOKEN}"}, method=method)
        except urllib.error.HTTPError as e:
            if e.code < 500 or n == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if n == tries - 1:
                raise
        time.sleep(1.5 * 2 ** n)


def gh_file(path):
    """(parsed json, sha) of a file in the race-stats repo, or (None, None)."""
    try:
        r = gh_call(f"https://api.github.com/repos/{REPO}/contents/{path}?ref=main")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    return json.loads(base64.b64decode(r["content"])), r["sha"]


def corrections():
    data, sha = gh_file("races/corrections.json")
    return (data or {"changes": []}), sha


_write_lock = threading.Lock()


def save_corrections(edit, message):
    """Reads corrections.json, applies edit(data) and commits it to main.
    Tries again when someone else committed in between."""
    save_json("races/corrections.json", edit, message, {"changes": []})


def save_json(path, edit, message, empty=None):
    """Reads a JSON file of race-stats, applies edit(data) and commits it to main."""
    with _write_lock:
        for attempt in range(5):
            data, sha = gh_file(path)
            data = data if data is not None else copy.deepcopy(empty)
            if data is None:
                raise ValueError(f"{path} is missing")
            edit(data)
            body = {"message": message, "branch": "main",
                    "content": base64.b64encode((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()).decode()}
            if sha:
                body["sha"] = sha
            try:
                gh_call(f"https://api.github.com/repos/{REPO}/contents/{path}", body, method="PUT")
                return
            except urllib.error.HTTPError as e:
                if e.code not in (409, 422):   # someone else committed in between: read again
                    raise
                time.sleep(1 + attempt)
        raise RuntimeError("Could not save: the file kept changing. Try again.")


# ---------------------------------------------------------------- login


def sign(value: bytes) -> str:
    return hmac.new(SECRET, value, hashlib.sha256).hexdigest()


def make_session(uid, name, kind="staff"):
    raw = base64.urlsafe_b64encode(json.dumps(
        {"id": str(uid), "name": name, "kind": kind, "exp": int(time.time()) + SESSION_DAYS * 86400}).encode()).decode()
    return f"{raw}.{sign(raw.encode())}"


def read_session(cookie, kind="staff"):
    """The session in a cookie, or None. A staff page needs a staff session;
    a protest takes any session (staff race too)."""
    try:
        raw, sig = cookie.rsplit(".", 1)
        if not hmac.compare_digest(sig, sign(raw.encode())):
            return None
        s = json.loads(base64.urlsafe_b64decode(raw))
        if kind == "staff" and s.get("kind", "staff") != "staff":
            return None
        return s if s["exp"] > time.time() else None
    except Exception:
        return None


def csrf_token(user):
    return sign(f"csrf:{user['id']}:{user['exp']}".encode())[:32]


def is_staff(uid: int) -> bool:
    """Owner and moderators from bot/staff.json, or anyone with a staff role in the server."""
    try:
        staff = json.loads(STAFF_FILE.read_text())
        if uid == staff.get("owner") or uid in staff.get("moderators", []):
            return True
    except Exception:
        pass
    if not BOT_TOKEN:
        return False
    auth = {"Authorization": f"Bot {BOT_TOKEN}"}
    try:
        member = http(f"https://discord.com/api/v10/guilds/{GUILD_ID}/members/{uid}", headers=auth)
        roles = http(f"https://discord.com/api/v10/guilds/{GUILD_ID}/roles", headers=auth)
    except urllib.error.HTTPError:
        return False
    names = {r["id"]: r["name"].lower() for r in roles}
    return any(any(w in names.get(r, "") for w in STAFF_ROLES) for r in member.get("roles", []))


def is_owner(uid: int) -> bool:
    try:
        return uid == json.loads(STAFF_FILE.read_text()).get("owner")
    except Exception:
        return False


def setup_key() -> str:
    """Key of the Setup manager data on the site (GreMi only). The site build
    encrypts with the same value, saved there as the SETUP_KEY secret."""
    return base64.b64encode(hmac.new(SECRET, b"setup-manager-v1", hashlib.sha256).digest()).decode()


def setup_key_page(user, q):
    if not is_owner(int(user["id"])):
        return page("Setup key", "<p>Only the owner can see this.</p>", user)
    return page("Setup key", "<div class=narrow><h1>Setup manager key</h1>"
                "<p class=muted>Save this once as a repository secret named <b>SETUP_KEY</b> in gremigaming/race-stats "
                "(Settings, Secrets and variables, Actions). The site uses it to lock your setups; only you get it "
                "after logging in on the Setup manager page. Don't share it.</p>"
                f"<p class=quote>{e(setup_key())}</p></div>", user)


def remember_name(uid, name):
    try:
        names = json.loads(NAMES_FILE.read_text()) if NAMES_FILE.exists() else {}
        names[str(uid)] = name
        NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        NAMES_FILE.write_text(json.dumps(names))
    except OSError:
        pass


def staff_name(uid):
    try:
        return json.loads(NAMES_FILE.read_text()).get(str(uid), "a moderator")
    except Exception:
        return "a moderator"


# ---------------------------------------------------------------- page bits

e = lambda s: html.escape(str(s if s is not None else ""), quote=True)

CSS = """
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;background:#09090b;color:#e4e4e7;font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:#fca5a5;text-decoration:none}a:hover{color:#fecaca}
header{display:flex;flex-wrap:wrap;gap:16px;align-items:center;padding:12px 20px;background:#18181b;position:sticky;top:0;z-index:2}
header b{font-size:15px;margin-right:8px}header nav{display:flex;gap:4px;flex-wrap:wrap}
header nav a{padding:6px 10px;border-radius:8px;color:#a1a1aa}header nav a.on{background:#27272a;color:#fff}
header .me{margin-left:auto;color:#71717a;font-size:12px}
header nav .grp{display:flex;gap:2px;align-items:center;padding-left:8px;margin-left:4px;border-left:1px solid #27272a}
header nav a{padding:5px 8px;font-size:13px}
header nav .gl{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#52525b;margin-right:4px}
main{max-width:1100px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:24px 0 8px;color:#d4d4d8}
.muted{color:#71717a;font-size:12px}.card{background:#18181b;border-radius:14px;padding:16px;margin:12px 0}
.grid{display:grid;grid-template-columns:240px 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.list a{display:block;padding:5px 8px;border-radius:6px;color:#d4d4d8;font-size:13px}.list a.on{background:#27272a;color:#fff}
.list .day{margin:12px 0 4px;color:#71717a;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
table{width:100%;border-collapse:collapse}td,th{padding:6px 8px;text-align:left;vertical-align:top;border-top:1px solid #27272a}
th{color:#71717a;font-weight:500;font-size:12px;border:0}
.neg{color:#fca5a5}.pos{color:#86efac}.zero{color:#71717a}.mono{font-family:ui-monospace,monospace}
input,select{background:#09090b;color:#e4e4e7;border:1px solid #3f3f46;border-radius:6px;padding:4px 6px;font:inherit;font-size:13px}
input[type=number]{width:70px}button{background:#3f3f46;color:#fff;border:0;border-radius:6px;padding:5px 10px;font:inherit;font-size:13px;cursor:pointer}
button:hover{background:#52525b}button.red{background:#991b1b}button.red:hover{background:#b91c1c}
form.inline{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:4px 0 0}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:99px;background:#1e3a8a;color:#bfdbfe;margin-left:6px}
.flash{background:#14532d;color:#bbf7d0;padding:10px 14px;border-radius:10px;margin-bottom:12px}
.err{background:#7f1d1d;color:#fecaca}
.login{max-width:380px;margin:12vh auto;text-align:center}
.login a.btn{display:inline-block;margin-top:16px;background:#5865f2;color:#fff;padding:10px 18px;border-radius:10px;font-weight:600}
.badge{display:inline-block;min-width:18px;padding:0 5px;margin-left:4px;border-radius:99px;background:#dc2626;color:#fff;font-size:11px;text-align:center}
.alert{background:#7f1d1d;color:#fecaca;padding:10px 14px;border-radius:10px;margin-bottom:12px}.alert a{color:#fff;font-weight:600}
textarea{width:100%;min-height:120px;background:#09090b;color:#e4e4e7;border:1px solid #3f3f46;border-radius:8px;padding:8px;font:inherit}
.narrow{max-width:640px;margin:0 auto}.quote{white-space:pre-wrap;border-left:3px solid #3f3f46;padding:4px 10px;margin:8px 0}
"""


def page(title, body, user=None, tab=""):
    nav = ""
    if user:
        n = open_protests()
        # three groups (GreMi, 5 Nov 2026): what needs doing, running the races, and setup
        groups = [("To do", [("protests", "Protests" + (f"<span class=badge>{n}</span>" if n else "")), ("grading", "Grading"),
                             ("grading/learned", "What I learned")]),
                  ("Race control", [("races", "Races"), ("drivers", "Drivers")]),
                  ("Admin", [("names", "Names"), ("members", "Members"), ("sr-settings", "SR settings"), ("log", "All changes")])]
        on = {"learned": "grading/learned"}.get(tab, tab)
        nav = (f'<nav><a href="/" class="{"on" if tab == "" else ""}">Home</a>'
               + "".join(f'<span class=grp><span class=gl>{g}</span>'
                         + "".join(f'<a href="/{p}" class="{"on" if p == on else ""}">{t}</a>' for p, t in links) + "</span>"
                         for g, links in groups) + "</nav>")
        nav += f'<span class="me">{e(user["name"])} · <a href="/logout">log out</a></span>'
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)} · GreMi Gang staff</title><style>{CSS}</style>{calls.STYLE}{calls.SCRIPT}</head><body>"
            + (f"<header><b>GreMi Gang staff</b>{nav}</header>" if user else "")
            + f"<main>{body}</main></body></html>")


def pts_class(v):
    return "neg" if v < 0 else "pos" if v > 0 else "zero"


def signed(v):
    v = float(v)
    return f"{'+' if v > 0 else '−' if v < 0 else '±'}{abs(v):g}"


def label(file):
    base = file.replace("_Just_in_case", "")
    m = re.match(r"^(.*?)_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})", base)
    if not m:
        return file
    kind, rest = ("Quali", m.group(1).split("Qualifying_", 1)[-1]) if "Qualifying" in m.group(1) else (
        "Race", m.group(1).split("Race_", 1)[-1]) if m.group(1).startswith("Race_") else (m.group(1).split("_")[0], m.group(1))
    return f"{kind} · {rest.replace('_', ' ')} · {m.group(5)}:{m.group(6)}"


def day_of(file):
    m = re.search(r"(\d{4})_(\d{2})_(\d{2})", file)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def field(name, value):
    return f'<input type=hidden name="{e(name)}" value="{e(value)}">'


def change_form(user, back, fields, inner, button="Save", cls=""):
    return (f'<form method=post action="/change" class="inline">{field("csrf", csrf_token(user))}{field("back", back)}'
            + "".join(field(k, v) for k, v in fields.items()) + inner
            + f'<input name=reason placeholder="Reason (shown on the site)" required maxlength=200 style="flex:1;min-width:180px">'
            + f'<button class="{cls}">{button}</button></form>')


def car_names(file):
    """{car index: driver name} for one session, from the published site."""
    d = site_json("league/sessions/" + urllib.parse.quote(file), ttl=600)
    return {c["index"]: c["driver-name"] for c in d.get("classification-data") or []}, d


# ---------------------------------------------------------------- pages


def home(user, q):
    data, _ = corrections()
    ch = list(reversed(data.get("changes", [])))[:8]
    rows = "".join(change_row(user, c) for c in ch) or "<tr><td class=muted>No changes yet.</td></tr>"
    n = open_protests()
    alert = (f"<div class=alert>{n} new protest{'s' if n > 1 else ''} from drivers. <a href='/protests'>Look at "
             f"{'them' if n > 1 else 'it'}</a></div>") if n else ""
    return page("Home", f"""{alert}
<h1>Hi {e(user['name'])}</h1>
<p class=muted>Changes go live on the site about 2 minutes after you save them. Every change can be undone under All changes.</p>
<div class=card><b><a href="/races">Races</a></b><div class=muted>Everything about one race: remove a test race, fix the results, make the call on every incident (all drivers in one row), other points and kicks.</div></div>
<div class=card><b><a href="/names">Names</a></b><div class=muted>Link an old online name to a driver's current one, or split names that were linked by mistake.</div></div>
<h2>Latest changes</h2><div class=card><table>{rows}</table></div>""", user, "")


def describe(c):
    t = c.get("type")
    who = c.get("driver") or ""
    if t == "incident":
        return f"SR item for {who} in {label(c.get('file', ''))}: {signed(c.get('was', 0))} → {signed(c['pts'])}"
    if t == "confirm":
        return f"Call kept for {who} in {label(c.get('file', ''))} ({signed(c.get('pts', 0))}): protest rejected"
    if t == "add":
        return f"{'Penalty' if float(c['pts']) < 0 else 'Bonus'} {signed(c['pts'])} for {who} in {label(c['file'])}: {c.get('text', '')}"
    if t == "result":
        what = {"time": f"+{float(c['value']):g} s time penalty", "status": f"status {c['value']}", "position": f"position P{c['value']}"}[c["kind"]]
        return f"{what} for {who} in {label(c['file'])}"
    if t == "alias":
        return f"Linked name “{c['from']}” to {c['to']}"
    if t == "split":
        return f"Unlinked name “{c['from']}”"
    if t == "unmerge":
        return f"Split hidden sessions from {who} ({c.get('merge', '').split('|')[0]} stream)"
    if t == "kick":
        n = len(c.get("cars", []))
        return f"Kicked {who} from the {c.get('day', '')} stream ({n} session{'s' if n != 1 else ''})"
    return t


def change_row(user, c):
    when = c.get("at", "")[:16].replace("T", " ")
    undo = (f'<form method=post action="/undo" class="inline">{field("csrf", csrf_token(user))}{field("id", c["id"])}'
            f'<button>Undo</button></form>')
    return (f"<tr><td>{e(describe(c))}<div class=muted>{e(c.get('reason', ''))}</div></td>"
            f"<td class=muted>{e(staff_name(c.get('by')))}<br>{e(when)} UTC</td><td>{undo}</td></tr>")


def incidents(user, q):
    sr = site_json("league/safety.json")
    streams = sr.get("streams", [])
    file = q.get("file", [""])[0]
    nav = ""
    for s in reversed(streams):
        nav += f'<div class=day>Stream {e(s["date"])}</div>'
        for f in reversed(s["files"]):
            nav += f'<a href="/incidents?file={urllib.parse.quote(f)}" class="{"on" if f == file else ""}">{e(label(f))}</a>'
    if not file:
        body = "<p class=muted>Pick a session on the left.</p>"
    else:
        body = incident_detail(user, sr, file)
    return page("Incidents", f"<h1>Incidents</h1><p class=muted>Safety rating points per session. Only sessions from 7 Oct 2026 on count.</p>"
                f"<div class=grid><div class='card list'>{nav}</div><div>{body}</div></div>", user, "incidents")


def incident_detail(user, sr, file):
    data, _ = corrections()
    pending = {c["id"]: c for c in data.get("changes", []) if c.get("type") == "incident"}
    back = f"/incidents?file={urllib.parse.quote(file)}"
    rows = []
    for d in sr.get("drivers", []):
        for s in d.get("sessions", []):
            if s["file"] != file:
                continue
            for it in s["items"]:
                rows.append((d["name"], it))
    rows.sort(key=lambda r: (r[1]["pts"] >= 0, r[1].get("lap") or 0, r[0].lower()))
    html_rows = ""
    for name, it in rows:
        if not it.get("id") or it["cat"] == "staff":
            continue
        p = pending.get(it["id"])
        note = ""
        if it.get("staff") is not None:
            note = f'<span class=tag>staff: was {signed(it.get("orig", 0))}</span>'
        elif p:
            note = f'<span class=tag>saved: {signed(p["pts"])}, live soon</span>'
        car = it["id"].split("|")[1]
        if it.get("cat") == "contact":
            # GreMi, 5 Nov 2026: change a contact like grading it (type, blame, severity), points follow; override kept
            kind = it.get("type") or calls.guess_type(None, it.get("text"))
            inner = (calls.picker(e, kind, "shared" if "shared" in (it.get("text") or "") else "fault",
                                  calls.severity_in(it.get("text")), it["pts"], guessed=bool(kind))
                     + "<div class=muted style='font-size:12px;width:100%'>Pick <i>Not at fault</i> to cancel the penalty. "
                       "Severity 1-3 = 1 point, 4-5 = 3, 6-7 = 6, 8-10 = 10 (v2.2 scale), shared = half, retaliation at 10 = 20.</div>")
        else:
            inner = f'<input type=number name=pts step=0.5 min=-50 max=10 value="{e(it["pts"])}" title="New points">'
        form = change_form(user, back, {"type": "incident", "id": it["id"], "file": file, "driver": name, "car": car,
                                        "was": it.get("orig", it["pts"])},
                           inner
                           + ('<label class=muted style="font-size:12px" title="The fault engine learns from this change: '
                              'it judged this contact wrongly. Untick for leniency (the call was right, the points are softened).">'
                              '<input type=checkbox name=learn value=1 checked> engine got it wrong (learn)</label>'
                              if it.get("cat") == "contact" else ""))
        extra = ((f"<span class=tag>{e(it['type'])}</span>" if it.get("type") else "") + calls.weather_badge(e, it.get("weather"))
                 + (f" <a href=\"{e(it['watch'])}\" target=_blank rel=noopener class=muted>▶ replay</a>" if it.get("watch") else ""))
        html_rows += (f"<tr><td class='mono {pts_class(it['pts'])}'>{signed(it['pts'])}</td><td><b>{e(name)}</b>"
                      f"{' · lap ' + e(it['lap']) if it.get('lap') else ''} · {e(it['cat'])}{note}{extra}<div class=muted>{e(it['text'])}</div>"
                      f"<details><summary class=muted style='cursor:pointer'>Change</summary>{form}</details></td></tr>")
    names, _ = car_names(file)
    opts = "".join(f'<option value="{i}">{e(n)}</option>' for i, n in sorted(names.items(), key=lambda x: x[1].lower())
                   if not AI_NAME.search(n))
    add = change_form(user, back, {"type": "add", "file": file},
                      f'<select name=car required>{opts}</select>'
                      f'<input type=number name=pts step=0.5 min=-50 max=10 value="-2" title="Points (minus for a penalty)">'
                      f'<input type=number name=lap min=1 max=99 placeholder="Lap" title="Lap (optional)">'
                      f'<input name=text placeholder="What happened" required maxlength=120>', "Add")
    return (collisions_card(file, sr) +
            f"<div class=card><h2 style='margin-top:0'>{e(label(file))}: points</h2>"
            f"<p class=muted>Points are typed on the v2.2 scale, as before (contacts 1 / 3 / 6 / 10, R9 20, track limits 1). The live SR v2.5 converts them: contacts x0.75 (0.75 / 2.25 / 4.5 / 7.5, R9 15), track limits / pit exit / DNF x1.5, bonuses x2, and the site shows the converted points. Set the new points (0 cancels it) and say why. Clearing a driver's only penalty in a race gives them their clean-lap points back.</p>"
            f"<table>{html_rows or '<tr><td class=muted>No points in this session.</td></tr>'}</table></div>"
            f"<div class=card><h2 style='margin-top:0'>Add a penalty or bonus</h2>{add}</div>")


def collisions_card(file, sr=None):
    """Every collision in the session (also the ones that cost nobody points), as on the site's race view."""
    try:
        incs = [i for i in site_json("league/incidents.json", ttl=300).get("incidents", []) if i.get("file") == file]
    except Exception as ex:
        return f"<div class=card><p class=muted>Collisions could not be loaded: {e(explain(ex))}</p></div>"
    incs.sort(key=lambda i: (i.get("lap") or 0, i.get("where") or ""))
    # the points list (safety.json, with staff changes) wins over the raw collision line,
    # exactly as on the website
    items = {}
    for d in (sr or {}).get("drivers", []):
        for se in d.get("sessions", []):
            if se["file"] == file:
                for it in se["items"]:
                    if it.get("cat") == "contact":
                        items.setdefault(it.get("replay") or (d["name"], it.get("lap"), it["text"].split(":", 1)[0]), {})[d["name"]] = it
    rows = ""
    for i in incs:
        names = i.get("names") or []
        sides = ""
        for n, (pts, why) in (i.get("lines") or {}).items():
            other = next((x for x in names if x != n), "")
            it = (items.get(i.get("replay")) or {}).get(n) if i.get("replay") else None
            it = it or (items.get((n, i.get("lap"), f"{i.get('where')}, with {other}")) or {}).get(n)
            if it:
                pts = it["pts"]
                why = it["text"].split(": ", 1)[-1] + (f" (staff: {it['staff']})" if it.get("staff") else "")
            sides += (f"<div><span class='mono {pts_class(pts)}'>{signed(pts)}</span> <b>{e(n)}</b>"
                      f"{' · <span class=muted>' + e(why) + '</span>' if why else ''}</div>")
        if not sides:
            sides = "<div class=muted>" + e(" vs ".join(i.get("names") or [])) + "</div>"
        sev = i.get("sev")
        badge = (f"<span class=tag>{e(i['type'])}</span>" if i.get("type") else "") + calls.weather_badge(e, i.get("weather"))
        if i.get("watch"):
            badge += f" <a href=\"{e(i['watch'])}\" target=_blank rel=noopener class=muted>▶ replay</a>"
        rows += (f"<tr><td class=mono>{'L' + e(i['lap']) if i.get('lap') else ''}</td>"
                 f"<td><b>{e(i.get('where') or 'Contact')}</b>"
                 f"{f' · <span class=muted>severity {e(sev)}</span>' if sev not in (None, '') else ''}"
                 f"{badge}"
                 f"{' · <span class=tag>replay on the site</span>' if i.get('replay') else ''}{sides}</td></tr>")
    return (f"<div class=card><h2 style='margin-top:0'>{e(label(file))}: incidents ({len(incs)})</h2>"
            f"<p class=muted>Every collision the telemetry found, with each driver's points and why. "
            f"Change a driver's points in the list below.</p>"
            f"<table>{rows or '<tr><td class=muted>No collisions in this session.</td></tr>'}</table></div>")


def results(user, q):
    idx = site_json("league/index.json", ttl=300)
    files = [f for f in idx.get("files", []) if f.startswith("Race_")]
    file = q.get("file", [""])[0]
    nav, last = "", ""
    for f in files[:120]:
        d = day_of(f)
        if d != last:
            nav += f"<div class=day>{e(d)}</div>"
            last = d
        nav += f'<a href="/results?file={urllib.parse.quote(f)}" class="{"on" if f == file else ""}">{e(label(f))}</a>'
    body = result_detail(user, file) if file else "<p class=muted>Pick a race on the left.</p>"
    return page("Results", f"<h1>Results</h1><p class=muted>Fix a race result. Points and positions are worked out again from your change.</p>"
                f"<div class=grid><div class='card list'>{nav}</div><div>{body}</div></div>", user, "results")


def result_detail(user, file):
    _, d = car_names(file)
    data, _ = corrections()
    pend = [c for c in data.get("changes", []) if c.get("type") == "result" and c.get("file") == file]
    back = f"/races?file={urllib.parse.quote(file)}#results"
    rows = sorted((c for c in d.get("classification-data", []) if c.get("final-classification")),
                  key=lambda c: c["final-classification"]["position"])
    trs = ""
    for c in rows:
        fc = c["final-classification"]
        form = change_form(user, back, {"type": "result", "file": file, "car": c["index"], "driver": c["driver-name"]},
                           "<select name=kind><option value=time>Time penalty (seconds)</option>"
                           "<option value=status_DISQUALIFIED>Disqualify</option><option value=status_DID_NOT_FINISH>Did not finish</option>"
                           "<option value=status_FINISHED>Finished (undo a DSQ or DNF)</option><option value=position>Set position</option></select>"
                           "<input type=number name=value min=0 max=300 placeholder='s or P' title='Seconds, or the new position'>")
        trs += (f"<tr><td class=mono>P{fc['position']}</td><td><b>{e(c['driver-name'])}</b>"
                f"<div class=muted>{e(fc.get('result-status'))} · {e(fc.get('total-race-time-str'))}"
                f"{' · +' + e(fc.get('penalties-time')) + ' s penalties' if fc.get('penalties-time') else ''} · {e(fc.get('points', 0))} pts</div>"
                f"<details><summary class=muted style='cursor:pointer'>Change</summary>{form}</details></td></tr>")
    pend_html = "".join(change_row(user, c) for c in pend)
    return (f"<div class=card><h2 style='margin-top:0'>Results</h2><table>{trs}</table></div>"
            + (f"<h2>Changes to this race</h2><div class=card><table>{pend_html}</table></div>" if pend else ""))


def names_page(user, q):
    aliases, _ = gh_file("races/aliases.json")
    aliases = {k: v for k, v in (aliases or {}).items() if not k.startswith("_")}
    data, _ = corrections()
    links = {}
    for k, v in aliases.items():
        links[k.lower()] = (k, v, None)
    for c in data.get("changes", []):
        if c.get("type") == "alias":
            links[c["from"].lower()] = (c["from"], c["to"], c)
        elif c.get("type") == "split":
            links.pop(c["from"].lower(), None)
    sr = site_json("league/safety.json")
    known = sorted({d["name"] for d in sr.get("drivers", [])}, key=str.lower)
    rows = ""
    for frm, to, c in sorted(links.values(), key=lambda x: x[1].lower()):
        btn = change_form(user, "/names", {"type": "split", "from": frm}, "", "Split", "red")
        rows += f"<tr><td>{e(frm)}</td><td>→ <b>{e(to)}</b></td><td style='width:55%'>{btn}</td></tr>"
    try:  # every named driver in the races (the site writes it at build)
        everyone = site_json("league/names.json").get("names") or known
    except Exception:
        everyone = known
    opts = "".join(f'<option value="{e(n)}">{e(n)}</option>' for n in everyone)
    pick = lambda name, ph: (f'<select name={name} required style="flex:1;min-width:180px">'
                             f'<option value="" selected disabled>{ph}</option>{opts}</select>')
    add = change_form(user, "/names", {"type": "alias"}, pick("from", "Old name") + pick("to", "Current name"), "Link")
    merged = ""
    try:
        merges = site_json("league/merges.json").get("merges", [])
    except Exception:  # the site hasn't published the list yet
        merges = []
    for m in reversed(merges):
        hidden = ", ".join(m.get("hidden", []))
        n = len(m.get("files", []))
        what = f"{e(hidden)} ({e(m['car'])}, {n} session{'s' if n != 1 else ''}) → <b>{e(m['name'])}</b>"
        if m.get("off"):
            btn = "<span class=muted>Split</span>"
        else:
            btn = change_form(user, "/names", {"type": "unmerge", "merge": m["id"], "from": m["name"]}, "", "Split", "red")
        merged += f"<tr><td>{e(m['day'])}</td><td>{what}</td><td style='width:45%'>{btn}</td></tr>"
    return page("Names", f"""<h1>Names</h1>
<p class=muted>When a driver changes their online name, link the old name to the new one so all their races, stats and safety rating land on one profile.</p>
<div class=card><h2 style='margin-top:0'>Link a name</h2>{add}</div>
<h2>Linked names</h2><div class=card><table>{rows or '<tr><td class=muted>None yet.</td></tr>'}</table></div>
<h2>Hidden names merged</h2>
<p class=muted>A hidden car that stopped showing up in a stream, followed by a named driver with the same race number, nationality and platform, is counted as that driver. Split one if it was someone else.</p>
<div class=card><table>{merged or '<tr><td class=muted>None yet.</td></tr>'}</table></div>""", user, "names")


def log_page(user, q):
    data, _ = corrections()
    rows = "".join(change_row(user, c) for c in reversed(data.get("changes", [])))
    return page("All changes", f"<h1>All changes</h1><p class=muted>Newest first. Undo removes a change; the site updates in about 2 minutes.</p>"
                f"<div class=card><table>{rows or '<tr><td class=muted>No changes yet.</td></tr>'}</table></div>", user, "log")


# ---------------------------------------------------------------- changes


def num(v, lo, hi):
    x = float(v)
    if not (lo <= x <= hi) or x != x:
        raise ValueError
    return x


def build_change(form, user):
    t = form.get("type")
    reason = (form.get("reason") or "").strip()[:200]
    if not reason:
        raise ValueError("Please give a reason.")
    c = {"id": secrets.token_hex(6), "type": t, "reason": reason, "by": user["id"],
         "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if t == "incident":
        if form.get("fault"):   # the picker: type, blame, severity -> points (or the override)
            call, pts = calls.read_call(form, -50, 10)
            c["call"] = call
        else:
            pts = num(form["pts"], -50, 10)
        c.update(id=form["id"], file=form["file"], driver=form.get("driver", ""), car=int(form["car"]),
                 pts=pts, was=num(form.get("was", 0), -100, 100))
        if "|contact|" in c["id"]:
            c["learn"] = form.get("learn") == "1"
            lesson_fields(c, user["id"])
    elif t == "add":
        c.update(file=form["file"], car=int(form["car"]), pts=num(form["pts"], -50, 10),
                 text=(form.get("text") or "").strip()[:120] or "Staff decision")
        if form.get("lap"):
            c["lap"] = int(num(form["lap"], 1, 99))
        names, _ = car_names(c["file"])
        c["driver"] = names.get(c["car"], "")
    elif t == "result":
        kind = form.get("kind", "")
        c.update(file=form["file"], car=int(form["car"]), driver=form.get("driver", ""))
        if kind.startswith("status_"):
            c.update(kind="status", value=kind.split("_", 1)[1])
            if c["value"] not in ("DISQUALIFIED", "DID_NOT_FINISH", "FINISHED"):
                raise ValueError
        elif kind == "time":
            c.update(kind="time", value=num(form.get("value"), 1, 300))
        elif kind == "position":
            c.update(kind="position", value=int(num(form.get("value"), 1, 30)))
        else:
            raise ValueError
    elif t == "alias":
        c.update({"from": form["from"].strip()[:60], "to": form["to"].strip()[:60]})
        if not c["from"] or not c["to"] or c["from"].lower() == c["to"].lower():
            raise ValueError("Give two different names.")
    elif t == "split":
        c["from"] = form["from"].strip()[:60]
    elif t == "unmerge":
        c.update(merge=form["merge"].strip()[:100], driver=form.get("from", "").strip()[:60])
        if not c["merge"]:
            raise ValueError
    elif t == "kick":
        day = form.get("day", "")
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", day):
            raise ValueError
        files = set(site_json("league/index.json", ttl=300).get("files", []))
        cars = []
        for v in (form.get("cars") or "").split("\n"):
            f, _, car = v.rpartition("|")
            if f in files and day.replace("-", "_") in f and car.isdigit():
                cars.append({"file": f, "car": int(car)})
        if not cars:
            raise ValueError("Tick at least one session.")
        c.update(day=day, driver=(form.get("driver") or "").strip()[:60], cars=cars)
    else:
        raise ValueError
    return c


def sr_item(item_id):
    """The SR item of the live data with this id (contact items carry their incident: time + pair)."""
    for d in site_json("league/safety.json").get("drivers", []):
        for s_ in d.get("sessions", []):
            for it in s_.get("items", []):
                if it.get("id") == item_id:
                    return it
    return None


def lesson_fields(c, uid):
    """A change of contact points is also a lesson for the fault engine (race-stats precedents.py):
    keep which contact it was (it survives renumbered items) and whether GreMi made it."""
    try:
        it = sr_item(c["id"])
    except Exception:
        it = None
    if it and it.get("inc"):
        c["inc"] = it["inc"]
        c.setdefault("text", (it.get("text") or "")[:200])
    c["owner"] = is_owner(int(uid or 0))


def backfill_lessons():
    """Older contact changes get their contact and the learn flag once (GreMi 3 Nov 2026: changes
    made that day were leniency, not wrong calls, so they don't teach the engine)."""
    if not GITHUB_TOKEN:
        return
    try:
        data, _ = corrections()
        todo = [c for c in data.get("changes", []) if c.get("type") == "incident" and "|contact|" in c.get("id", "")
                and ("learn" not in c or "owner" not in c)]
        if not todo:
            return
        refs = {c["id"]: sr_item(c["id"]) for c in todo if "inc" not in c}

        def edit(d):
            for c in d.get("changes", []):
                if c.get("type") != "incident" or "|contact|" not in c.get("id", ""):
                    continue
                if "learn" not in c:
                    c["learn"] = not (c.get("at") or "").startswith("2026-11-03")
                it = refs.get(c["id"])
                if "inc" not in c and it and it.get("inc"):
                    c["inc"] = it["inc"]
                    c.setdefault("text", (it.get("text") or "")[:200])
                if "owner" not in c:
                    c["owner"] = is_owner(int(c.get("by") or 0))
        save_corrections(edit, f"Staff: {len(todo)} contact changes kept as fault-engine lessons")
        print("staff page: contact changes backfilled as lessons", flush=True)
    except Exception as ex:
        print("staff page: lessons not backfilled:", ex, flush=True)


def apply_change(c):
    def edit(data):
        ch = data.setdefault("changes", [])
        if c["type"] == "incident":  # one entry per item; the newest wins
            ch[:] = [x for x in ch if not (x.get("type") == "incident" and x["id"] == c["id"])]
        if c["type"] == "split":  # unlinking a name linked here just removes that link
            linked = [x for x in ch if x.get("type") == "alias" and x["from"].lower() == c["from"].lower()]
            if linked:
                ch[:] = [x for x in ch if x not in linked]
                return
        if c in ch:   # already saved by a try that GitHub answered with an error
            return
        ch.append(c)
    save_corrections(edit, f"Staff: {describe(c)}"[:120])


def undo_change(cid):
    def edit(data):
        data["changes"] = [x for x in data.get("changes", []) if x.get("id") != cid]
    save_corrections(edit, "Staff: undo a change")


def kick_page(user, q):
    idx = site_json("league/index.json", ttl=300)
    files = [f for f in idx.get("files", []) if f.startswith("Race_") or "Qualifying" in f]
    days = sorted({day_of(f) for f in files}, reverse=True)[:30]
    day = q.get("day", [days[0] if days else ""])[0]
    nav = "".join(f'<a href="/kick?day={e(d)}" class="{"on" if d == day else ""}">{e(d)}</a>' for d in days)
    data, _ = corrections()
    done = [c for c in data.get("changes", []) if c.get("type") == "kick" and c.get("day") == day]
    cars = {}  # driver name -> [(file, car)]
    for f in sorted((f for f in files if day_of(f) == day), key=lambda f: re.search(r"\d{4}_\d\d_\d\d_\d\d_\d\d", f).group(0)):
        names, _ = car_names(f)
        for car, n in names.items():
            cars.setdefault(n, []).append((f, car))
    back = f"/kick?day={day}"
    rows = ""
    for n, ss in sorted(cars.items(), key=lambda x: (bool(AI_NAME.search(x[0])), x[0].lower())):
        boxes = "".join(f'<label style="display:block"><input type=checkbox name=cars value="{e(f)}|{car}" checked> {e(label(f))}</label>'
                        for f, car in ss)
        hidden = " <span class=muted>(name hidden: the same team and number can be someone else in another race)</span>" if AI_NAME.search(n) else ""
        form = (f'<form method=post action="/change" onsubmit="return confirm(\'Kick {e(n)} from the {e(day)} stream?\')">'
                f'{field("csrf", csrf_token(user))}{field("back", back)}{field("type", "kick")}{field("day", day)}{field("driver", n)}'
                f'<div style="margin:6px 0">{boxes}</div><div class=inline style="display:flex;gap:6px">'
                f'<input name=reason placeholder="Reason (shown on the site)" required maxlength=200 style="flex:1">'
                f'<button class=red>Kick</button></div></form>')
        rows += f"<tr><td><b>{e(n)}</b>{hidden}<details><summary class=muted style='cursor:pointer'>Kick</summary>{form}</details></td></tr>"
    done_html = "".join(change_row(user, c) for c in done)
    body = (f"<div class=card><h2 style='margin-top:0'>Stream of {e(day)}</h2>"
            f"<p class=muted>Kicking a driver removes them from the sessions you tick: their result, and every contact, penalty "
            f"and overtake with them, for everyone. The others move up a place. The kicked driver still loses safety rating "
            f"for their own incidents.</p><table>{rows or '<tr><td class=muted>No sessions that day.</td></tr>'}</table></div>"
            + (f"<h2>Kicked from this stream</h2><div class=card><table>{done_html}</table></div>" if done else ""))
    return page("Kick", f"<h1>Kick a driver</h1><div class=grid><div class='card list'>{nav}</div><div>{body}</div></div>", user, "kick")


# ---------------------------------------------------------------- protests

_protest_lock = threading.Lock()


def protests():
    try:
        return json.loads(PROTESTS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return []


def save_protests(edit):
    with _protest_lock:
        data = protests()
        out = edit(data)
        PROTESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROTESTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        tmp.replace(PROTESTS_FILE)
        return out


def open_protests():
    return sum(p["status"] == "open" for p in protests())


def find_item(item_id):
    """(driver, session file, item) of one safety rating item on the site, or None."""
    for d in site_json("league/safety.json").get("drivers", []):
        for s in d.get("sessions", []):
            for it in s.get("items", []):
                if it.get("id") == item_id:
                    return d["name"], s["file"], it
    return None


def member_link(uid):
    """The member's link {driver, how, at}, or None."""
    try:
        from bot import links as L
        return L.Links().get(uid)
    except Exception:
        return None


def other_in(text):
    """The other driver of a contact, from "..., with NAME: ..."."""
    m = re.search(r"\bwith (.+?):", text or "")
    return m.group(1) if m else None


def involved(driver, it):
    return [d for d in (driver, other_in(it.get("text"))) if d]


def is_involved(link, driver, it):
    return bool(link) and link["driver"].lower() in {d.lower() for d in involved(driver, it)}


KINDS = ["Contact", "Divebomb or late lunge", "Pushed off track", "Unsafe rejoin", "Blocking or moving under braking",
         "Track limits or corner cutting", "Lag or netcode", "Game penalty was wrong", "Other"]


ANSWER = {"open": "Waiting for the stewards", "accepted": "Accepted", "rejected": "Not accepted"}


def protest_card(p, staff=None, back="/protests"):
    """One protest; with staff set, the steward's buttons too."""
    head = (f"<b>{e(p['driver'])}</b> · {e(label(p['file']))}{' · lap ' + e(p['lap']) if p.get('lap') else ''}"
            f" · <span class='mono neg'>{signed(p['pts'])}</span>")
    out = f"<div class=card>{head}<div class=muted>{e(p['what'])}</div>"
    if staff:
        linked = p.get("linked")
        who = ("from the site, name not checked" if p.get("site")
               else "the penalised driver" if linked and linked.lower() == p["driver"].lower()
               else f"the other driver, {linked}" if linked else "not linked to a driver")
        if p.get("linked_now"):
            who += ", linked their name just before this protest"
        out += f"<div class=muted style='margin-top:8px'>Sent by {e(p['by_name'])} ({e(who)}) · {e(p['at'][:16].replace('T', ' '))} UTC</div>"
    said = " · ".join(x for x in (p.get("kind") and f"Type: {p['kind']}", p.get("fault") and f"At fault: {p['fault']}") if x)
    if said:
        out += f"<div style='margin-top:8px'>{e(said)}</div>"
    out += f"<div class=quote>{e(p['text'])}</div>"
    if staff and p.get("clip_file"):
        out += (f"<video controls preload=metadata src='/clip/{e(p['id'])}' style='width:100%;max-width:640px;border-radius:8px'></video>"
                f"<div><a href='/clip/{e(p['id'])}' target=_blank>Open the clip</a></div>")
    elif p.get("clip_file"):
        out += "<div class=muted>Clip uploaded</div>"
    if p.get("clip"):
        out += f'<div><a href="{e(p["clip"])}" target=_blank rel="noopener noreferrer nofollow">Video clip</a></div>'
    if p["status"] != "open":
        out += f"<p><b>{e(ANSWER[p['status']])}</b>{': ' + e(p['answer']) if p.get('answer') else ''}</p>"
    elif staff:
        out += protest_grade_form(p, staff, back)
    else:
        out += f"<p class=muted>{e(ANSWER['open'])}</p>"
    return out + "</div>"


def protest_grade_form(p, staff, back="/protests"):
    """Grade the protested incident like any other (GreMi, 5 Nov 2026): type, blame and severity
    for the penalised driver. Accept = the points follow from the call (or the override);
    reject = the engine's call stays. Both teach the fault engine."""
    try:
        found = find_item(p["item"])
    except Exception:
        found = None
    it = found[2] if found else {}
    text = it.get("text") or p.get("what") or ""
    kind = it.get("type") or calls.guess_type(None, text)
    head = ((f"<span class=tag>{e(kind)}</span>" if kind else "") + calls.weather_badge(e, it.get("weather"))
            + (f" <a class=btn href=\"{e(races.viewer_url(p['file'], it.get('lap'), involved(p['driver'], it)))}\" target=_blank rel=noopener>▶ Incident viewer</a>"
               if p.get("file") and it else ""))
    data = calls.profiles()
    profs = "".join(calls.profile_html(e, calls.profile_of(n, data), short=True) for n in dict.fromkeys(involved(p["driver"], it or {"text": text})))
    base = f'{field("csrf", csrf_token(staff))}{field("id", p["id"])}{field("back", back)}'
    sev = calls.severity_in(text)
    return (f"<div style='margin-top:8px'>{head}</div>"
            + (f"<details><summary class=muted style='cursor:pointer'>Driver summaries (for context, never for the call)</summary>{profs}</details>" if profs else "")
            + f"<form method=post action='/protest-answer'>{base}"
            f"<p class=muted style='margin:8px 0 0'>Grade the call on <b>{e(p['driver'])}</b> as you see it on the replay. "
            f"<b>Accept</b> sets the points from your call (pick <i>Not at fault</i> to cancel the penalty); <b>Reject</b> keeps the "
            f"current points. Either way the fault engine learns from it, and the driver sees your answer. "
            f"<a href='/races?file={urllib.parse.quote(p['file'])}#incidents'>All incidents in this race</a></p>"
            + calls.picker(e, kind, "fault", sev, p.get("pts"), guessed=bool(kind), lo=-50, hi=0)
            + "<div class=inline style='display:flex;gap:6px;flex-wrap:wrap'>"
            "<input name=answer placeholder='Answer to the driver' required maxlength=300 style='flex:1;min-width:220px'>"
            "<button name=action value=accept>Accept, set points from the call</button>"
            "<button name=action value=reject class=red>Reject, keep the points</button></div></form>")


def protests_page(user, q):
    ps = sorted(protests(), key=lambda p: p["at"], reverse=True)
    opn = [p for p in ps if p["status"] == "open"]
    done = [p for p in ps if p["status"] != "open"][:30]
    return page("Protests", "<h1>Protests</h1><p class=muted>Drivers who don't agree with an incident on the site. "
                "Accept to change the points, or reject; either way the driver sees your answer.</p>"
                + "".join(protest_card(p, user) for p in reversed(opn))
                + ("" if opn else "<div class=card><span class=muted>No open protests.</span></div>")
                + (f"<h2>Answered</h2>" + "".join(protest_card(p, user) for p in done) if done else ""), user, "protests")


def protest_answer(form, user):
    pid, action = form.get("id", ""), form.get("action")
    answer = (form.get("answer") or "").strip()[:300]
    if action not in ("accept", "reject") or not answer:
        raise ValueError("Please write an answer.")
    p = next((x for x in protests() if x["id"] == pid), None)
    if not p or p["status"] != "open":
        raise ValueError("That protest was already answered.")
    call = None
    if form.get("fault"):   # the grading picker (older forms send only points)
        call, pts = calls.read_call(form, -50, 0)
    else:
        pts = num(form.get("pts", 0), -50, 0)
    if action == "reject" and call and call["fault"] == "clear":
        raise ValueError("Rejecting keeps the penalty: pick At fault or Shared blame, or accept the protest.")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if action == "accept":
        c = {"id": p["item"], "type": "incident", "file": p["file"], "driver": p["driver"], "car": int(p["item"].split("|")[1]),
             "pts": pts, "was": p["pts"], "reason": f"Protest accepted: {answer}"[:200], "by": user["id"], "at": now,
             **({"call": call} if call else {})}
        if "|contact|" in c["id"]:
            c["learn"] = True   # an accepted protest = the engine judged it wrongly
            lesson_fields(c, user["id"])
        apply_change(c)
    elif "|contact|" in p["item"]:
        # GreMi, 5 Nov 2026: a rejected protest teaches the engine too (its call was right). Kept as a
        # "confirm" change: the safety rating ignores it, race-stats precedents.py learns from it.
        c = {"id": p["item"], "type": "confirm", "file": p["file"], "driver": p["driver"], "car": int(p["item"].split("|")[1]),
             "pts": p["pts"], "was": p["pts"], "reason": f"Protest rejected: {answer}"[:200], "by": user["id"], "at": now,
             "learn": True, **({"call": {k: v for k, v in call.items() if k != "override"}} if call else {})}
        lesson_fields(c, user["id"])
        try:
            apply_change(c)
        except Exception as ex:   # the answer to the driver matters more than the lesson
            print("protest: rejected call not kept as a lesson:", ex, flush=True)

    def edit(data):
        for x in data:
            if x["id"] == pid:
                x.update(status="accepted" if action == "accept" else "rejected", answer=answer, closed_by=user["id"],
                         closed_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    save_protests(edit)
    return "Answered. The site updates in about 2 minutes." if action == "accept" else "Answered."


def protest_page(member, q):
    """What a driver sees: the incident and the protest form, or their protests.
    Only the drivers in an incident can protest it; an unlinked member picks their name first."""
    item = q.get("item", [""])[0]
    if not member:
        nxt = urllib.parse.quote("/protest" + (f"?item={urllib.parse.quote(item)}" if item else ""))
        return page("Protest", f"<div class=login><h1>Protest an incident</h1><p class=muted>Log in with Discord so the stewards "
                    f"know which driver you are and can answer you.</p><a class=btn href='/login?next={nxt}'>Log in with Discord</a></div>")
    mine = [p for p in protests() if p["by"] == member["id"]]
    link = member_link(member["id"])
    top = (f"<p class=muted>Logged in as {e(member['name'])}{' (driver ' + e(link['driver']) + ')' if link else ''} · "
           f"<a href='/protest'>Your protests</a> · <a href='{SITE}'>Race stats</a> · <a href='/logout'>log out</a></p>")
    if not item:
        cards = "".join(protest_card(p) for p in sorted(mine, key=lambda p: p["at"], reverse=True))
        return page("Your protests", f"<div class=narrow><h1>Your protests</h1>{top}"
                    + (cards or "<div class=card><span class=muted>You haven't protested anything yet. Open your safety rating on the site "
                       "and use the flag next to an incident.</span></div>") + "</div>")
    sent = next((p for p in mine if p["item"] == item), None)
    if sent:
        return page("Protest", f"<div class=narrow><h1>Your protest</h1>{top}{protest_card(sent)}</div>")
    found = find_item(item)
    if not found:
        return page("Protest", f"<div class=narrow><h1>Protest</h1>{top}<div class=card>This incident isn't on the site any more. "
                    "It may have been changed already.</div></div>")
    driver, file, it = found
    card = (f"<div class=card><b>{e(driver)}</b> · {e(label(file))}"
            f"{' · lap ' + e(it['lap']) if it.get('lap') else ''} · <span class='mono neg'>{signed(it['pts'])}</span>"
            f"<div class=muted>{e(it['text'])}</div></div>")
    if it["pts"] >= 0 or it.get("staff") is not None:
        return page("Protest", f"<div class=narrow><h1>Protest</h1>{top}{card}<div class=card>Only penalties the stewards haven't "
                    "looked at yet can be protested.</div></div>")
    csrf = field("csrf", csrf_token(member)) + field("item", item)
    if not link:
        from bot import links as L
        data = L.Links().read()
        taken = {v["driver"].lower() for v in data.values() if v["how"] != "auto"}
        names = sorted((d for d in L.drivers_of(site_json("league/safety.json")) if d.lower() not in taken), key=str.lower)
        opts = "".join(f"<option{' selected' if d in involved(driver, it) and len(involved(driver, it)) == 1 else ''}>{e(d)}</option>"
                       for d in names)
        return page("Protest", f"<div class=narrow><h1>Protest an incident</h1>{top}{card}<div class=card>"
                    f"<p><b>Your Discord isn't linked to a driver yet.</b> Pick your driver name. It's the same as /link in "
                    f"LINK-YOUR-NAME on Discord and also gets you your safety rating role.</p>"
                    f'<form method=post action="/protest-link" class=inline>{csrf}<select name=driver required>'
                    f'<option value="" selected disabled>Your driver name</option>{opts}</select><button>This is me</button></form>'
                    f"<p class=muted>Name not in the list? Someone else linked it. Ask a mod.</p></div></div>")
    if not is_involved(link, driver, it):
        who = " and ".join(involved(driver, it))
        return page("Protest", f"<div class=narrow><h1>Protest</h1>{top}{card}<div class=card>Only the drivers in this incident "
                    f"({e(who)}) can protest it. Your Discord is linked to {e(link['driver'])}. Wrong name? Ask a mod.</div></div>")
    other = other_in(it["text"])
    faults = ["Me"] + ([other] if other and other.lower() != link["driver"].lower() else [])
    if other and other.lower() == link["driver"].lower():
        faults.append(driver)
    faults += ["No one, racing incident", "Someone else"]
    sel = lambda name, opts, ph: (f'<select name={name} required style="width:100%"><option value="" selected disabled>{ph}</option>'
                                  + "".join(f"<option>{e(o)}</option>" for o in opts) + "</select>")
    form = (f'<form method=post action="/protest" id=pf>{csrf}'
            f'<p><b>Who was at fault?</b></p>{sel("fault", faults, "Choose")}'
            f'<p><b>What kind of incident?</b></p>{sel("kind", KINDS, "Choose")}'
            f'<p><b>What happened?</b></p><textarea name=text required minlength=10 maxlength=1500 '
            f'placeholder="Tell us what happened from your side, and why you don\'t agree."></textarea>'
            f'<p><b>Video clip</b> <span class=muted>(optional, up to {CLIP_MAX // 1024 ** 2} MB)</span></p>'
            f'<p><input type=file id=clip accept="video/mp4,video/webm,video/quicktime,video/x-matroska"></p>'
            f'<p id=pmsg class=muted></p><button id=pbtn>Send to the stewards</button></form>' + PROTEST_JS)
    return page("Protest", f"<div class=narrow><h1>Protest an incident</h1>{top}{card}<div class=card>{form}</div></div>")


# Sends the form, then the clip with a progress count. Without a clip the form posts as usual.
PROTEST_JS = """<script>
document.getElementById('pf').addEventListener('submit', async function (ev) {
  var f = document.getElementById('clip').files[0];
  if (!f) return;
  ev.preventDefault();
  var msg = document.getElementById('pmsg'), btn = document.getElementById('pbtn');
  if (f.size > %d) { msg.textContent = 'That clip is too big. Trim it, or put a link in the description.'; return; }
  btn.disabled = true; btn.textContent = 'Sending...';
  var r = await fetch('/protest', {method: 'POST', headers: {'Accept': 'application/json'}, body: new URLSearchParams(new FormData(this))});
  var d = await r.json();
  if (!d.ok) { msg.textContent = d.message; btn.disabled = false; btn.textContent = 'Send to the stewards'; return; }
  var x = new XMLHttpRequest();
  x.open('POST', '/api/protest-clip?id=' + d.id + '&token=' + d.token);
  x.setRequestHeader('Content-Type', f.type);
  x.upload.onprogress = function (e) { if (e.lengthComputable) btn.textContent = 'Uploading clip ' + Math.round(e.loaded / e.total * 100) + '%%'; };
  var done = function (t) { location.href = location.pathname + location.search + '&ok=' + encodeURIComponent(t); };
  x.onload = function () { var j = {}; try { j = JSON.parse(x.responseText); } catch (e) {}
    done(j.ok ? 'Sent with your clip. The stewards get a notice and will answer here.' : 'Sent, but the clip did not upload' + (j.message ? ' (' + j.message + ')' : '') + '. Share it with a mod on Discord.'); };
  x.onerror = function () { done('Sent, but the clip did not upload. Share it with a mod on Discord.'); };
  x.send(f);
});
</script>""" % CLIP_MAX


def protest_link(form, member):
    """An unlinked member picks their driver name on the protest page (a self link, like /link)."""
    from bot import links as L
    driver = form.get("driver", "")
    if driver not in L.drivers_of(site_json("league/safety.json")):
        raise ValueError("Pick your driver name from the list.")
    ok, msg = L.Links().link(int(member["id"]), driver, "self")
    if not ok:
        raise ValueError(msg)
    return msg


def clip_upload(pid, token, ctype, length, stream):
    """Stores the video clip of a protest, once, shortly after it was sent."""
    ext = CLIP_TYPES.get(ctype.split(";")[0].strip().lower())
    if not ext:
        raise ValueError("Only mp4, webm, mov or mkv videos.")
    if not re.fullmatch(r"[0-9a-f]{12}", pid or "") or not hmac.compare_digest(token or "", sign(f"clip:{pid}".encode())):
        raise ValueError("Bad upload.")
    if length <= 0 or length > CLIP_MAX:
        raise ValueError(f"The clip is too big. The limit is {CLIP_MAX // 1024 ** 2} MB, so trim it or share a link.")
    p = next((x for x in protests() if x["id"] == pid), None)
    if not p or p.get("clip_file"):
        raise ValueError("Bad upload.")
    age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(p["at"].replace("Z", "+00:00"))
    if age.total_seconds() > 3600:
        raise ValueError("Too late to add a clip to this protest.")
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    if sum(f.stat().st_size for f in CLIPS_DIR.iterdir()) + length > CLIPS_TOTAL:
        raise ValueError("The stewards' clip storage is full. Share a link to the clip instead.")
    tmp = CLIPS_DIR / f".{pid}.part"
    left = length
    with open(tmp, "wb") as f:
        while left:
            chunk = stream.read(min(left, 1 << 20))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        tmp.unlink(missing_ok=True)
        raise ValueError("The upload was cut off. Try again.")
    tmp.rename(CLIPS_DIR / f"{pid}.{ext}")

    def edit(data):
        for x in data:
            if x["id"] == pid:
                x["clip_file"] = f"{pid}.{ext}"
    save_protests(edit)


def protest_send(form, member):
    """Saves a protest from a logged-in driver who was in the incident. Returns its id."""
    item = form.get("item", "")
    text = (form.get("text") or "").strip()[:1500]
    fault = (form.get("fault") or "").strip()[:60]
    kind = (form.get("kind") or "").strip()[:60]
    if len(text) < 10:
        raise ValueError("Please tell us a bit more.")
    found = find_item(item)
    if not found or found[2]["pts"] >= 0:
        raise ValueError("That incident isn't on the site any more.")
    driver, file, it = found
    link = member_link(member["id"])
    if not is_involved(link, driver, it):
        raise ValueError("Only the drivers in this incident can protest it.")
    pid = secrets.token_hex(6)
    now = datetime.datetime.now(datetime.timezone.utc)

    def edit(data):
        mine = [p for p in data if p["by"] == member["id"]]
        if any(p["item"] == item for p in mine):
            raise ValueError("You already protested this incident.")
        if sum(p["status"] == "open" for p in mine) >= 10:
            raise ValueError("You have 10 open protests. Wait for the stewards to answer those first.")
        data.append({"id": pid, "item": item, "driver": driver, "file": file, "lap": it.get("lap"),
                     "pts": it["pts"], "what": it["text"], "by": member["id"], "by_name": member["name"],
                     "linked": link["driver"], "linked_now": link.get("how") == "self" and now.timestamp() - link.get("at", 0) < 3600,
                     "fault": fault, "kind": kind, "text": text, "clip": "", "status": "open",
                     "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    save_protests(edit)
    return pid


# ---------------------------------------------------------------- server

def guild_members(ttl=600):
    """{discord id: display name} of the server's members, cached."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get("members")
        if hit and now - hit[0] < ttl:
            return hit[1]
    out, after = {}, 0
    for _ in range(10):
        page_ = http(f"https://discord.com/api/v10/guilds/{GUILD_ID}/members?limit=1000&after={after}",
                     headers={"Authorization": f"Bot {BOT_TOKEN}"})
        for m in page_:
            if not m["user"].get("bot"):
                out[int(m["user"]["id"])] = m.get("nick") or m["user"].get("global_name") or m["user"]["username"]
        if len(page_) < 1000:
            break
        after = page_[-1]["user"]["id"]
    with _cache_lock:
        _cache["members"] = (now, out)
    return out


def members_page(user, q):
    from bot import links as L
    store = L.Links()
    data = store.read()
    people = guild_members()
    drivers = L.drivers_of(site_json("league/safety.json"))
    how = {"auto": "matched by name", "self": "linked themself", "staff": "linked by staff"}
    rows = ""
    for uid, v in sorted(data.items(), key=lambda x: x[1]["driver"].lower()):
        d = drivers.get(v["driver"])
        rank = f"{L.rank_of(d['sr'])} · {d['sr']:.1f}" if d else "no rating"
        unlink = (f'<form method=post action="/member" class="inline">{field("csrf", csrf_token(user))}'
                  f'{field("action", "unlink")}{field("uid", uid)}<button class=red>Unlink</button></form>')
        rows += (f"<tr><td><b>{e(v['driver'])}</b><div class=muted>{e(rank)}</div></td>"
                 f"<td>{e(people.get(int(uid), 'left the server'))}<div class=muted>{e(how.get(v['how'], v['how']))}</div></td>"
                 f"<td>{unlink}</td></tr>")
    linked = {v["driver"].lower() for v in data.values()}
    missing = [n for n in drivers if n.lower() not in linked]
    opts = "".join(f'<option value="{uid}">{e(n)}</option>' for uid, n in sorted(people.items(), key=lambda x: x[1].lower()))
    dl = "".join(f'<option value="{e(n)}">' for n in sorted(drivers, key=str.lower))
    form = (f'<form method=post action="/member" class="inline">{field("csrf", csrf_token(user))}{field("action", "link")}'
            f'<select name=uid required>{opts}</select><input name=driver list=drivers placeholder="Driver name" required>'
            f'<datalist id=drivers>{dl}</datalist><button>Link</button></form>')
    return page("Members", f"""<h1>Members</h1>
<p class=muted>Which Discord member drives under which name. The bot links members by itself when their Discord name
matches a driver exactly or almost, and members can use <b>/link</b>. A link set here replaces any other link to that name.</p>
<div class=card><h2 style='margin-top:0'>Link a member</h2>{form}</div>
<h2>Drivers with a rating but no member ({len(missing)})</h2><div class=card>{e(', '.join(missing)) or 'None.'}</div>
<h2>Linked ({len(data)})</h2><div class=card><table>{rows or '<tr><td class=muted>Nobody yet.</td></tr>'}</table></div>""",
                user, "members")


def member_change(form, user):
    from bot import links as L
    store = L.Links()
    uid = int(form["uid"])
    if form.get("action") == "unlink":
        store.unlink(uid)
        return "Unlinked. The bot updates their role within 10 minutes."
    drivers = L.drivers_of(site_json("league/safety.json"))
    name = next((n for n in drivers if n.lower() == form.get("driver", "").strip().lower()), None)
    if not name:
        raise ValueError("Pick a driver name from the list.")
    store.unlink(uid)
    store.link(uid, name, "staff", by=int(user["id"]))
    return f"Linked to {name}. The bot updates their role within 10 minutes."


# ---------------------------------------------------------------- setup manager
# GreMi's versioned in-game setups (race-stats setups/registry.json). The
# Setup manager page links here: Finish / Reopen a track, or start the Apply
# run that saves the waiting versions in the game (docs/setup-apply.md).
SETUP_REGISTRY = "setups/registry.json"
SETUP_PAGE = SITE
ROUTINE_ID = os.environ.get("ROUTINE_ID", "")
ROUTINE_TOKEN = os.environ.get("ROUTINE_FIRE_TOKEN", "")
SETUP_ACTIONS = {"finish": "Finish setup", "reopen": "Reopen setup", "apply": "Apply in the game"}


def fire_apply_run():
    """Wakes the Race Control routine for the Apply run (computer use on GreMi's PC)."""
    if not (ROUTINE_ID and ROUTINE_TOKEN):
        raise ValueError("the routine isn't set up on this server (ROUTINE_ID, ROUTINE_FIRE_TOKEN)")
    http(f"https://api.anthropic.com/v1/claude_code/routines/{ROUTINE_ID}/fire",
         {"text": ("Setup Apply run requested by the owner on the Setup manager.\n"
                   "Follow docs/setup-apply.md in gremigaming/race-stats: save the waiting setup "
                   "versions of setups/registry.json in F1 25 on GreMi's PC.")},
         headers={"anthropic-beta": "experimental-cc-routine-2026-04-01", "anthropic-version": "2023-06-01",
                  "Content-Type": "application/json", "Authorization": f"Bearer {ROUTINE_TOKEN}"})


def setup_track(reg, name):
    t = next((t for t in reg.get("tracks", []) if t["name"] == name), None)
    if not t:
        raise ValueError("unknown track")
    return t


def setup_action_page(user, q):
    if not is_owner(int(user["id"])):
        return page("Not allowed", "<p>Only GreMi can change his setups.</p>", user)
    done = q.get("ok", [""])[0]
    site = q.get("site", [""])[0]
    site = site if site.startswith(SITE) else SETUP_PAGE
    back = f"<p><a class=btn href='{e(site)}'>Back to the Setup manager</a></p>"
    if done:
        return page("Setup manager", f"<div class=narrow>{back}</div>", user)
    act, name = q.get("do", [""])[0], q.get("track", [""])[0]
    if act not in SETUP_ACTIONS:
        return page("Setup manager", "<p>Unknown action.</p>" + back, user)
    reg, _ = gh_file(SETUP_REGISTRY)
    if not reg:
        return page("Setup manager", "<p>No setup registry yet.</p>" + back, user)
    if act == "apply":
        waiting = [f"{t['name']} - {k.upper()} - V{t['kinds'][k]['versions'][-1]['v']}: {t['kinds'][k]['versions'][-1]['text']}"
                   for t in sorted(reg["tracks"], key=lambda t: t["row"]) for k in ("quali", "race", "wet")
                   if not t["kinds"][k]["versions"][-1].get("saved")]
        if not waiting:
            return page("Setup manager", "<p>Nothing waits: every version is in the game.</p>" + back, user)
        what = ("<p>Claude starts F1 25 on your PC and saves these versions over the old ones. "
                "Keep the PC on with the Claude app open, and the game closed.</p><ul>"
                + "".join(f"<li>{e(w)}</li>" for w in waiting) + "</ul>")
    else:
        t = setup_track(reg, name)
        what = (f"<p>{e(t['name'])}: no new versions after this; the newest saved versions stay.</p>" if act == "finish"
                else f"<p>{e(t['name'])}: new versions again after the next stream.</p>")
    form = (f"<form method=post action='/setup-action'><input type=hidden name=csrf value='{csrf_token(user)}'>"
            f"<input type=hidden name=do value='{e(act)}'><input type=hidden name=track value='{e(name)}'>"
            f"<input type=hidden name=site value='{e(site)}'>"
            f"<button class=btn>{e(SETUP_ACTIONS[act])}</button></form>")
    return page("Setup manager", f"<div class=narrow><h2>{e(SETUP_ACTIONS[act])}</h2>{what}{form}{back}</div>", user)


def setup_action_post(form, user):
    if not is_owner(int(user["id"])):
        raise ValueError("only GreMi can change his setups")
    act, name = form.get("do", ""), form.get("track", "")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if act in ("finish", "reopen"):
        def edit(reg):
            setup_track(reg, name)["finished"] = now[:10] if act == "finish" else None
        save_json(SETUP_REGISTRY, edit, f"Setup manager: {name} {'finished' if act == 'finish' else 'reopened'}")
        return f"{name}: {'finished' if act == 'finish' else 'reopened'}. The page updates in about 3 minutes."
    if act == "apply":
        def edit(reg):
            reg.setdefault("apply", {})["requested"] = now
        save_json(SETUP_REGISTRY, edit, "Setup manager: Apply run requested")
        fire_apply_run()
        return "Apply run started. Claude saves the setups in the game on your PC and reports in Discord."
    raise ValueError("unknown action")


def owner_id():
    try:
        return int(json.loads(STAFF_FILE.read_text()).get("owner") or 0)
    except Exception:
        return 0


def sync_decisions():
    """Final calls on graded incidents -> race-stats grading/decisions.json (fault engine precedents)."""
    from staff import grading
    try:
        if owner_id() and GITHUB_TOKEN and grading.sync_decisions(owner_id(), gh_file, save_json):
            print("grading: decisions saved to race-stats", flush=True)
    except Exception as ex:
        print("grading: decisions not saved:", ex, flush=True)


def open_protest_cards(user):
    """Open protests for the top of the grading page (GreMi: the incidents we don't agree on matter most)."""
    ps = sorted((p for p in protests() if p["status"] == "open"), key=lambda p: p["at"])
    return "".join(protest_card(p, user, "/grading") for p in ps)


def _grading_helpers():
    return {"e": e, "page": page, "csrf_token": csrf_token, "is_owner": is_owner, "protests": open_protest_cards}


def grading_page(user, q):
    from staff import grading
    return grading.grading_page(user, q, _grading_helpers())


def grading_results(user, q):
    from staff import grading
    return grading.results_page(user, q, _grading_helpers())


def grade_post(form, user):
    from staff import grading
    inc = grading.incident(form.get("id", ""))
    if not inc:
        raise ValueError("unknown incident")
    if grading.involved(inc, grading.linked_driver(int(user["id"]))):
        raise ValueError("you drove in this incident, so another steward grades it")
    g = grading.save(inc, int(user["id"]), user["name"], form)
    threading.Thread(target=sync_decisions, daemon=True).start()
    return f"Saved {inc['id'].upper()}: {grading.verdict_of(g)}."


def sr_settings_page(user, q):
    from staff import sr_settings
    data, _ = gh_file(sr_settings.PATH)
    try:
        live = site_json("league/safety.json", ttl=30).get("settings") or {}
    except Exception:
        live = None
    return page("SR settings", sr_settings.page_body(e, data or {}, live, is_owner(int(user["id"])), csrf_token(user)),
                user, "sr-settings")


def sr_settings_post(form, user):
    from staff import sr_settings
    if not is_owner(int(user["id"])):
        raise ValueError("only GreMi can change the SR settings")
    done = {}

    def edit(data):
        done.clear()
        done.update(sr_settings.post(form, data, user["name"]))
    if not form.get("do"):   # bad values: stop before anything goes to GitHub
        sr_settings.from_form(form, sr_settings.current(None))
    save_json(sr_settings.PATH, edit, f"SR settings: {(form.get('reason') or '').strip()[:80]} ({user['name']})", {"values": {}})
    if not done:
        return "Nothing changed."
    return f"Saved {len(done)} change{'s' if len(done) > 1 else ''}. The website updates in about 3 minutes, the live data within about 10."


PAGES = {"/": home, "/grading": grading_page, "/grading/results": grading_results,
         "/grading/learned": lambda user, q: calls.learned_page(user, q, _grading_helpers()),
         "/drivers": lambda user, q: calls.drivers_page(user, q, _grading_helpers()), "/races": races.races_page, "/names": names_page,
         "/members": members_page, "/log": log_page, "/kick": kick_page, "/protests": protests_page,
         "/setup-key": setup_key_page, "/setup-action": setup_action_page, "/sr-settings": sr_settings_page}


def explain(ex):
    """Says which outside service refused, and which key to check."""
    if isinstance(ex, urllib.error.HTTPError):
        url = ex.url or ""
        if "oauth2/token" in url:
            return (f"Discord refused the login ({ex.code}). Check the Discord client secret and that the redirect "
                    f"{BASE_URL}/callback is saved on the Discord OAuth2 page. To type the secret again: "
                    "run the setup script with --keys.")
        if "api.github.com" in url and ex.code >= 500:
            return (f"GitHub had a temporary problem ({ex.code}), also after trying again for half a minute. "
                    "Nothing was changed. Try again in a few minutes.")
        if "api.github.com" in url and ex.code in (401, 403):
            return (f"GitHub refused the token ({ex.code}). Make a new token with access to race-stats "
                    "(Contents: read and write) and run the setup script with --keys.")
        if "api.github.com" in url:
            return f"GitHub said {ex.code} ({url.split('?')[0].split('/repos/', 1)[-1]})."
        if "discord.com" in url:
            return f"Discord said {ex.code} ({url.split('/api/v10', 1)[-1].split('?')[0]})."
        return f"{url.split('?')[0]} said {ex.code}."
    return f"{type(ex).__name__}: {ex}"


class Handler(BaseHTTPRequestHandler):
    server_version = "GreMiStaff/1"

    def log_message(self, fmt, *args):  # no IPs or query strings in the journal
        pass

    def cookie(self, name):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[name].value if name in c else None

    def user(self):
        return read_session(self.cookie("staff") or "")

    def member(self):
        return read_session(self.cookie("member") or "", kind="member")

    def send(self, code, body="", headers=None):
        raw = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        for k, v in {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                     "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin", **(headers or {})}.items():
            if isinstance(v, list):
                for x in v:
                    self.send_header(k, x)
            else:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, to, cookies=None):
        self.send(303, "", {"Location": to, **({"Set-Cookie": cookies} if cookies else {})})

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        try:
            if url.path in ("/giveaway", "/giveaway.json"):
                import importlib
                from bot import giveaway
                giveaway = importlib.reload(giveaway)  # the bot's code may be newer than this page
            if url.path == "/giveaway":  # OBS browser source, public
                return self.send(200, giveaway.OVERLAY.read_text(encoding="utf-8"),
                                 {"X-Frame-Options": "SAMEORIGIN"})
            if url.path == "/giveaway.json":
                body = giveaway.public(giveaway.read_state(), giveaway.load_config(), time.time())
                return self.send(200, json.dumps(body), {"Content-Type": "application/json",
                                                         "Access-Control-Allow-Origin": "*"})
            if url.path in ("/socials", "/socials.json"):  # OBS stats overlay, public
                import importlib
                from bot import socials_live
                socials_live = importlib.reload(socials_live)
                if url.path == "/socials":
                    return self.send(200, socials_live.OVERLAY.read_text(encoding="utf-8"),
                                     {"X-Frame-Options": "SAMEORIGIN"})
                body = socials_live.public(socials_live.read_state(), time.time())
                return self.send(200, json.dumps(body), {"Content-Type": "application/json",
                                                         "Access-Control-Allow-Origin": "*"})
            if url.path == "/setup-login":  # the Setup manager on the site
                return self.login(setup_back=q.get("back", [""])[0])
            if url.path == "/login":
                return self.login()
            if url.path == "/callback":
                return self.callback(q)
            if url.path == "/logout":
                return self.redirect("/", ["staff=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
                                           "member=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax"])
            if url.path == "/protest":
                body = protest_page(self.member(), q)
                flash = q.get("ok", [""])[0]
                if flash:
                    body = body.replace("<main>", f"<main><div class=flash>{e(flash)}</div>", 1)
                return self.send(200, body)
            user = self.user()
            if not user:
                login_to = "/login?next=" + urllib.parse.quote(self.path) if url.path == "/setup-action" else "/login"
                msg = {"denied": "<p class='flash err'>That Discord account isn't on the staff list.</p>"}.get(q.get("m", [""])[0], "")
                return self.send(200, page("Log in", f"<div class=login><h1>GreMi Gang staff</h1>{msg}"
                                           f"<p class=muted>For moderators only.</p><a class=btn href='{e(login_to)}'>Log in with Discord</a></div>"))
            if url.path == "/grading/export.json":
                if not is_owner(int(user["id"])):
                    return self.send(403, page("Not allowed", "<p>Only the owner can export grades.</p>", user))
                from staff import grading
                return self.send(200, grading.export(int(user["id"])), {"Content-Type": "application/json; charset=utf-8",
                                 "Content-Disposition": "attachment; filename=staff-grades.json"})
            if url.path.startswith("/clip/"):
                return self.send_clip(url.path[6:])
            if url.path in ("/incidents", "/results"):   # merged into Races (GreMi, 6 Nov 2026)
                f = q.get("file", [""])[0]
                return self.redirect("/races" + (f"?file={urllib.parse.quote(f)}" + ("#incidents" if url.path == "/incidents" else "#results") if f else ""))
            fn = PAGES.get(url.path)
            if not fn:
                return self.send(404, page("Not found", "<p>Not found.</p>", user))
            body = fn(user, q)
            flash = q.get("ok", [""])[0]
            if flash:
                body = body.replace("<main>", f"<main><div class=flash>{e(flash)}</div>", 1)
            self.send(200, body)
        except Exception as ex:  # keep the page up and say what went wrong
            self.send(500, page("Error", f"<p class='flash err'>Something went wrong: {e(explain(ex))}</p>", self.user()))

    def api_clip(self, q):
        cors = {"Content-Type": "application/json"}
        length = int(self.headers.get("Content-Length") or 0)
        try:
            clip_upload(q.get("id", [""])[0], q.get("token", [""])[0], self.headers.get("Content-Type", ""), length, self.rfile)
            return self.send(200, json.dumps({"ok": True}), cors)
        except ValueError as ex:
            self.close_connection = True  # the body may be unread
            return self.send(400, json.dumps({"ok": False, "message": str(ex)}), cors)
        except Exception:
            self.close_connection = True
            return self.send(500, json.dumps({"ok": False, "message": "The clip didn't save. Share a link instead."}), cors)

    def send_clip(self, pid):
        """A protest clip, for staff only, with Range so the video player can seek."""
        p = next((x for x in protests() if x["id"] == pid), None)
        if not p or not p.get("clip_file"):
            return self.send(404, page("Not found", "<p>Not found.</p>", self.user()))
        path = CLIPS_DIR / p["clip_file"]
        size = path.stat().st_size
        start, end, code = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and size and (m[1] or m[2]):
            if m[1]:
                start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
            else:
                start = max(0, size - int(m[2]))
            if start > end:
                return self.send(416, "", {"Content-Range": f"bytes */{size}"})
            code = 206
        ctype = {v: k for k, v in CLIP_TYPES.items()}[path.suffix[1:]]
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(left, 1 << 20))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/protest-clip":
            return self.api_clip(urllib.parse.parse_qs(url.query))
        length = min(int(self.headers.get("Content-Length") or 0), 60000)
        multi = urllib.parse.parse_qs(self.rfile.read(length).decode())
        form = {k: v[0] for k, v in multi.items()}
        form["cars"] = "\n".join(multi.get("cars", []))
        form["fault"] = "\n".join(multi.get("fault", []))
        for k, v in multi.items():   # grading: reason tags of every split part
            if k.endswith("_tag"):
                form[k] = "\n".join(v)
        if self.path == "/protest":
            member = self.member()
            back = "/protest?item=" + urllib.parse.quote(form.get("item", ""))
            if not member or not hmac.compare_digest(form.get("csrf", ""), csrf_token(member)):
                return self.redirect(back)
            wants_json = "application/json" in self.headers.get("Accept", "")
            try:
                pid = protest_send(form, member)
                if wants_json:  # the page uploads the clip next
                    return self.send(200, json.dumps({"ok": True, "id": pid, "token": sign(f"clip:{pid}".encode())}),
                                     {"Content-Type": "application/json"})
                msg = "Sent. The stewards get a notice and will answer here."
            except Exception as ex:
                msg = f"Not sent: {ex if isinstance(ex, ValueError) else explain(ex)}"
                if wants_json:
                    return self.send(200, json.dumps({"ok": False, "message": msg}), {"Content-Type": "application/json"})
            return self.redirect(f"{back}&ok={urllib.parse.quote(msg)}")
        if self.path == "/protest-link":
            member = self.member()
            back = "/protest?item=" + urllib.parse.quote(form.get("item", ""))
            if not member or not hmac.compare_digest(form.get("csrf", ""), csrf_token(member)):
                return self.redirect(back)
            try:
                msg = protest_link(form, member) + " You can protest now."
            except Exception as ex:
                msg = f"Not linked: {ex if isinstance(ex, ValueError) else explain(ex)}"
            return self.redirect(f"{back}&ok={urllib.parse.quote(msg)}")
        user = self.user()
        if not user or not hmac.compare_digest(form.get("csrf", ""), csrf_token(user)):
            return self.send(403, page("Not allowed", "<p>Please log in again.</p>"))
        back = form.get("back", "/")
        if not back.startswith("/") or back.startswith("//"):
            back = "/"
        try:
            if self.path == "/change":
                c = build_change(form, user)
                apply_change(c)
                msg = "Saved. The site updates in about 2 minutes."
            elif self.path == "/incident-call":
                msg = races.incident_post(form, user)
            elif self.path == "/race-remove":
                msg = races.remove_post(form, user)
                back = "/races?removed=1"
            elif self.path == "/race-restore":
                msg = races.restore_post(form, user)
                back = "/races?removed=1"
            elif self.path == "/member":
                back = "/members"
                msg = member_change(form, user)
            elif self.path == "/undo":
                undo_change(form.get("id", ""))
                back, msg = "/log", "Undone. The site updates in about 2 minutes."
            elif self.path == "/grade":
                back = "/grading?id=" + urllib.parse.quote(form.get("id", ""))
                msg = grade_post(form, user)
            elif self.path == "/protest-answer":
                back = back if back.startswith("/grading") else "/protests"
                msg = protest_answer(form, user)
            elif self.path == "/sr-settings":
                back = "/sr-settings"
                msg = sr_settings_post(form, user)
            elif self.path == "/setup-action":
                back = "/setup-action?site=" + urllib.parse.quote(form.get("site", ""))
                msg = setup_action_post(form, user)
            else:
                return self.send(404, "")
        except (ValueError, KeyError) as ex:
            msg = f"Not saved: {str(ex) or 'check the values'}"
        except Exception as ex:
            msg = f"Not saved: {explain(ex)}"
        back, _, frag = back.partition("#")   # the flash goes before the #spot on the page
        sep = "&" if "?" in back else "?"
        self.redirect(f"{back}{sep}ok={urllib.parse.quote(msg)}" + (f"#{frag}" if frag else ""))

    def login(self, setup_back=""):
        state = secrets.token_urlsafe(16)
        q = urllib.parse.urlencode({"client_id": CLIENT_ID, "redirect_uri": BASE_URL + "/callback", "response_type": "code",
                                    "scope": "identify", "state": state})
        nxt = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("next", [""])[0]
        cookies = [f"staff_state={state}; Max-Age=600; Path=/; HttpOnly; Secure; SameSite=Lax"]
        if nxt.startswith("/protest") or nxt.startswith("/setup-action"):  # a protest, or GreMi's setup buttons
            cookies.append(f"staff_next={urllib.parse.quote(nxt, safe='')}; Max-Age=600; Path=/; HttpOnly; Secure; SameSite=Lax")
        if setup_back:  # GreMi opening the Setup manager: back to the site afterwards
            if not setup_back.startswith(SITE):
                return self.send(400, "Unknown return address")
            cookies.append(f"setup_back={urllib.parse.quote(setup_back, safe='')}; Max-Age=600; Path=/; HttpOnly; Secure; SameSite=Lax")
        self.redirect(f"https://discord.com/oauth2/authorize?{q}", cookies)

    def callback(self, q):
        state, code = q.get("state", [""])[0], q.get("code", [""])[0]
        if not code or not state or not hmac.compare_digest(state, self.cookie("staff_state") or ""):
            return self.redirect("/")
        tok = http("https://discord.com/api/v10/oauth2/token",
                   {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code",
                    "code": code, "redirect_uri": BASE_URL + "/callback"},
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        me = http("https://discord.com/api/v10/users/@me", headers={"Authorization": f"Bearer {tok['access_token']}"})
        uid = int(me["id"])
        clear = ["staff_state=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
                 "staff_next=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
                 "setup_back=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax"]
        back = urllib.parse.unquote(self.cookie("setup_back") or "")
        if back.startswith(SITE):  # the key goes in the hash, so no server ever logs it
            ok = is_owner(uid)
            return self.redirect(back + ("#setup-key=" + urllib.parse.quote(setup_key()) if ok else "#setup-denied"), clear)
        nxt = urllib.parse.unquote(self.cookie("staff_next") or "")
        nxt = nxt if nxt.startswith("/protest") or nxt.startswith("/setup-action") else ""
        name = me.get("global_name") or me.get("username") or "Driver"
        age = f"Max-Age={SESSION_DAYS * 86400}; Path=/; HttpOnly; Secure; SameSite=Lax"
        member = f"member={make_session(uid, name, 'member')}; {age}"
        if not is_staff(uid):
            return self.redirect(nxt, clear + [member]) if nxt else self.redirect("/?m=denied", clear)
        remember_name(uid, name)
        self.redirect(nxt or "/", clear + [member, f"staff={make_session(uid, name)}; {age}"])


def reload_on_change():
    """The bot pulls main when new code arrives; start again when this file changed."""
    files = [pathlib.Path(__file__), pathlib.Path(__file__).with_name("grading.py"), pathlib.Path(__file__).with_name("sr_settings.py"),
             pathlib.Path(__file__).with_name("calls.py"), pathlib.Path(__file__).with_name("races.py")]
    stamp = lambda: [f.stat().st_mtime if f.exists() else 0 for f in files]  # noqa: E731
    seen = stamp()
    while True:
        time.sleep(60)
        try:
            if stamp() != seen:
                print("staff page: new code, starting again", flush=True)
                os._exit(0)  # systemd (Restart=always) starts it again
        except OSError:
            pass


def main():
    threading.Thread(target=reload_on_change, daemon=True).start()
    threading.Thread(target=sync_decisions, daemon=True).start()   # grades made before this existed
    threading.Thread(target=backfill_lessons, daemon=True).start()
    missing = [k for k in ("DISCORD_CLIENT_SECRET", "STAFF_GITHUB_TOKEN", "STAFF_SECRET") if not os.environ.get(k)]
    if missing:
        print("staff page: missing " + ", ".join(missing), flush=True)
    print(f"staff page on 127.0.0.1:{PORT} for {BASE_URL}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
