"""Staff page for the GreMi Gang race stats site.

Moderators log in with Discord and fix what the automatic data gets wrong:
safety rating incidents, results and driver names. Every change is one entry
in races/corrections.json in gremigaming/race-stats; the site applies it the
next time it builds (about 2 minutes) and any entry can be undone.

Runs behind Caddy (HTTPS) on the Race Control server, standard library only.
Needs in the environment: DISCORD_CLIENT_SECRET, DISCORD_BOT_TOKEN,
STAFF_GITHUB_TOKEN, STAFF_SECRET and STAFF_URL (the public https address).
Run with: python3 -m staff.app
"""
import base64
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
UA = "DiscordBot (https://github.com/gremigaming/race-control-stats, 1.0)"
SESSION_DAYS = 14
AI_NAME = re.compile(r" #\d+$")  # AI and hidden names

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


def site_json(path, ttl=60):
    """A file from the published site, cached for ttl seconds."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
    data = http(SITE + path + f"?t={int(now)}")
    with _cache_lock:
        if len(_cache) > 60:
            _cache.clear()
        _cache[path] = (now, data)
    return data


GH = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def gh_file(path):
    """(parsed json, sha) of a file in the race-stats repo, or (None, None)."""
    try:
        r = http(f"https://api.github.com/repos/{REPO}/contents/{path}?ref=main",
                 headers={**GH, "Authorization": f"Bearer {GITHUB_TOKEN}"})
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
    with _write_lock:
        for _ in range(3):
            data, sha = corrections()
            edit(data)
            body = {"message": message, "branch": "main",
                    "content": base64.b64encode((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()).decode()}
            if sha:
                body["sha"] = sha
            try:
                http(f"https://api.github.com/repos/{REPO}/contents/races/corrections.json", body,
                     headers={**GH, "Authorization": f"Bearer {GITHUB_TOKEN}"}, method="PUT")
                return
            except urllib.error.HTTPError as e:
                if e.code not in (409, 422):
                    raise
        raise RuntimeError("Could not save: the file kept changing. Try again.")


# ---------------------------------------------------------------- login


def sign(value: bytes) -> str:
    return hmac.new(SECRET, value, hashlib.sha256).hexdigest()


def make_session(uid, name):
    raw = base64.urlsafe_b64encode(json.dumps(
        {"id": str(uid), "name": name, "exp": int(time.time()) + SESSION_DAYS * 86400}).encode()).decode()
    return f"{raw}.{sign(raw.encode())}"


def read_session(cookie):
    try:
        raw, sig = cookie.rsplit(".", 1)
        if not hmac.compare_digest(sig, sign(raw.encode())):
            return None
        s = json.loads(base64.urlsafe_b64decode(raw))
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
"""


def page(title, body, user=None, tab=""):
    nav = ""
    if user:
        links = [("", "Home"), ("incidents", "Incidents"), ("results", "Results"), ("names", "Names"),
                 ("members", "Members"), ("log", "All changes")]
        nav = "<nav>" + "".join(f'<a href="/{p}" class="{"on" if p == tab else ""}">{t}</a>' for p, t in links) + "</nav>"
        nav += f'<span class="me">{e(user["name"])} · <a href="/logout">log out</a></span>'
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)} · GreMi Gang staff</title><style>{CSS}</style></head><body>"
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
    return page("Home", f"""
<h1>Hi {e(user['name'])}</h1>
<p class=muted>Changes go live on the site about 2 minutes after you save them. Every change can be undone under All changes.</p>
<div class=card><b><a href="/incidents">Incidents</a></b><div class=muted>Change or cancel the safety rating points of an incident, or add a penalty or bonus the game missed.</div></div>
<div class=card><b><a href="/results">Results</a></b><div class=muted>Give a time penalty, disqualify someone or set a finishing position.</div></div>
<div class=card><b><a href="/names">Names</a></b><div class=muted>Link an old online name to a driver's current one, or split names that were linked by mistake.</div></div>
<h2>Latest changes</h2><div class=card><table>{rows}</table></div>""", user, "")


def describe(c):
    t = c.get("type")
    who = c.get("driver") or ""
    if t == "incident":
        return f"SR item for {who} in {label(c.get('file', ''))}: {signed(c.get('was', 0))} → {signed(c['pts'])}"
    if t == "add":
        return f"{'Penalty' if float(c['pts']) < 0 else 'Bonus'} {signed(c['pts'])} for {who} in {label(c['file'])}: {c.get('text', '')}"
    if t == "result":
        what = {"time": f"+{float(c['value']):g} s time penalty", "status": f"status {c['value']}", "position": f"position P{c['value']}"}[c["kind"]]
        return f"{what} for {who} in {label(c['file'])}"
    if t == "alias":
        return f"Linked name “{c['from']}” to {c['to']}"
    if t == "split":
        return f"Unlinked name “{c['from']}”"
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
        form = change_form(user, back, {"type": "incident", "id": it["id"], "file": file, "driver": name, "car": car,
                                        "was": it.get("orig", it["pts"])},
                           f'<input type=number name=pts step=0.5 min=-25 max=10 value="{e(it["pts"])}" title="New points">')
        html_rows += (f"<tr><td class='mono {pts_class(it['pts'])}'>{signed(it['pts'])}</td><td><b>{e(name)}</b>"
                      f"{' · lap ' + e(it['lap']) if it.get('lap') else ''} · {e(it['cat'])}{note}<div class=muted>{e(it['text'])}</div>"
                      f"<details><summary class=muted style='cursor:pointer'>Change</summary>{form}</details></td></tr>")
    names, _ = car_names(file)
    opts = "".join(f'<option value="{i}">{e(n)}</option>' for i, n in sorted(names.items(), key=lambda x: x[1].lower())
                   if not AI_NAME.search(n))
    add = change_form(user, back, {"type": "add", "file": file},
                      f'<select name=car required>{opts}</select>'
                      f'<input type=number name=pts step=0.5 min=-25 max=10 value="-2" title="Points (minus for a penalty)">'
                      f'<input type=number name=lap min=1 max=99 placeholder="Lap" title="Lap (optional)">'
                      f'<input name=text placeholder="What happened" required maxlength=120>', "Add")
    return (f"<div class=card><h2 style='margin-top:0'>{e(label(file))}</h2>"
            f"<p class=muted>Set the new points (0 cancels it) and say why. Clearing a driver's only penalty in a race gives them the clean race bonus back.</p>"
            f"<table>{html_rows or '<tr><td class=muted>No points in this session.</td></tr>'}</table></div>"
            f"<div class=card><h2 style='margin-top:0'>Add a penalty or bonus</h2>{add}</div>")


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
    back = f"/results?file={urllib.parse.quote(file)}"
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
    return (f"<div class=card><h2 style='margin-top:0'>{e(label(file))}</h2><table>{trs}</table></div>"
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
    dl = "".join(f'<option value="{e(n)}">' for n in known)
    add = change_form(user, "/names", {"type": "alias"},
                      f'<input name=from placeholder="Old name, exactly as in the race" required maxlength=60>'
                      f'<input name=to list=known placeholder="Current name" required maxlength=60><datalist id=known>{dl}</datalist>', "Link")
    return page("Names", f"""<h1>Names</h1>
<p class=muted>When a driver changes their online name, link the old name to the new one so all their races, stats and safety rating land on one profile.</p>
<div class=card><h2 style='margin-top:0'>Link a name</h2>{add}</div>
<h2>Linked names</h2><div class=card><table>{rows or '<tr><td class=muted>None yet.</td></tr>'}</table></div>""", user, "names")


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
        c.update(id=form["id"], file=form["file"], driver=form.get("driver", ""), car=int(form["car"]),
                 pts=num(form["pts"], -25, 10), was=num(form.get("was", 0), -100, 100))
    elif t == "add":
        c.update(file=form["file"], car=int(form["car"]), pts=num(form["pts"], -25, 10),
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
    else:
        raise ValueError
    return c


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
        ch.append(c)
    save_corrections(edit, f"Staff: {describe(c)}"[:120])


def undo_change(cid):
    def edit(data):
        data["changes"] = [x for x in data.get("changes", []) if x.get("id") != cid]
    save_corrections(edit, "Staff: undo a change")


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


PAGES = {"/": home, "/incidents": incidents, "/results": results, "/names": names_page,
         "/members": members_page, "/log": log_page}


def explain(ex):
    """Says which outside service refused, and which key to check."""
    if isinstance(ex, urllib.error.HTTPError):
        url = ex.url or ""
        if "oauth2/token" in url:
            return (f"Discord refused the login ({ex.code}). Check the Discord client secret and that the redirect "
                    f"{BASE_URL}/callback is saved on the Discord OAuth2 page. To type the secret again: "
                    "run the setup script with --keys.")
        if "api.github.com" in url:
            return (f"GitHub refused the token ({ex.code}). Make a new token with access to race-stats "
                    "(Contents: read and write) and run the setup script with --keys.")
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
            if url.path == "/login":
                return self.login()
            if url.path == "/callback":
                return self.callback(q)
            if url.path == "/logout":
                return self.redirect("/", ["staff=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax"])
            user = self.user()
            if not user:
                msg = {"denied": "<p class='flash err'>That Discord account isn't on the staff list.</p>"}.get(q.get("m", [""])[0], "")
                return self.send(200, page("Log in", f"<div class=login><h1>GreMi Gang staff</h1>{msg}"
                                           f"<p class=muted>For moderators only.</p><a class=btn href='/login'>Log in with Discord</a></div>"))
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

    def do_POST(self):
        user = self.user()
        length = min(int(self.headers.get("Content-Length") or 0), 20000)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode()).items()}
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
            elif self.path == "/member":
                back = "/members"
                msg = member_change(form, user)
            elif self.path == "/undo":
                undo_change(form.get("id", ""))
                back, msg = "/log", "Undone. The site updates in about 2 minutes."
            else:
                return self.send(404, "")
        except (ValueError, KeyError) as ex:
            msg = f"Not saved: {str(ex) or 'check the values'}"
        except Exception as ex:
            msg = f"Not saved: {explain(ex)}"
        sep = "&" if "?" in back else "?"
        self.redirect(f"{back}{sep}ok={urllib.parse.quote(msg)}")

    def login(self):
        state = secrets.token_urlsafe(16)
        q = urllib.parse.urlencode({"client_id": CLIENT_ID, "redirect_uri": BASE_URL + "/callback", "response_type": "code",
                                    "scope": "identify", "state": state})
        self.redirect(f"https://discord.com/oauth2/authorize?{q}",
                      [f"staff_state={state}; Max-Age=600; Path=/; HttpOnly; Secure; SameSite=Lax"])

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
        clear = "staff_state=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax"
        if not is_staff(uid):
            return self.redirect("/?m=denied", [clear])
        name = me.get("global_name") or me.get("username") or "Moderator"
        remember_name(uid, name)
        self.redirect("/", [clear, f"staff={make_session(uid, name)}; Max-Age={SESSION_DAYS * 86400}; Path=/; HttpOnly; Secure; SameSite=Lax"])


def main():
    missing = [k for k in ("DISCORD_CLIENT_SECRET", "STAFF_GITHUB_TOKEN", "STAFF_SECRET") if not os.environ.get(k)]
    if missing:
        print("staff page: missing " + ", ".join(missing), flush=True)
    print(f"staff page on 127.0.0.1:{PORT} for {BASE_URL}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
