"""Incident grading for the staff: calibrates the fault engine with more eyes than GreMi's.

Batches come from the site (league/grading.json: five picks per stream, written by the site build,
each with Claude's take that only GreMi sees) and from staff/grading/ (batch-NN.json, made by hand): incidents with a replay link and the fault engine's call. Staff grade blind
(they never see the engine's call or anyone else's grade), and never grade an incident
they drove in (their /link). GreMi sees everything on the owner-only results page;
his grade is final, otherwise the staff majority counts.

Grades are kept on this server only: GRADES_FILE, {incident id: {discord id: grade}}.

A grade can split an incident into parts: each part has a time window on the replay clock, a call
per driver (at fault / shared blame / involved, not at fault / not involved) with its own
severity and note, and reason tags. The fault engine (race-stats scripts/telemetry/precedents.py)
turns every part into its own incident and precedent; shared blame costs half the points in the SR.
staff/grading/fixes.json holds corrections of GreMi's grades made outside the page (used until he
grades that incident again).
"""
import collections
import contextlib
import fcntl
import json
import os
import pathlib
import re
import threading
import time

from staff import calls

BATCH_DIR = pathlib.Path(__file__).resolve().parent / "grading"
GRADES_FILE = pathlib.Path(os.environ.get("STAFF_GRADES_FILE", "/var/lib/race-control/grades.json"))
NO, RACING = "Not an incident", "Racing incident"
PICKS = {"low": "low severity", "high": "high severity", "gray": "gray area"}
CALLS = {"fault": "At fault", "shared": "Shared blame (half points)", "clear": "Involved, not at fault",
         "out": "Not involved"}
TAGS = ["Too fast into the corner", "Moved across / squeezed", "Early turn-in", "Lost control", "Ran into the back",
        "Divebomb / unsafe overtake", "Didn't leave room", "Unsafe rejoin", "Reckless after a crash",
        "Brake test / erratic", "Chain reaction", "Started the chaos"]
MAX_PARTS = 8


# ---------------------------------------------------------------- data


def stream_batches():
    """The five picks after every stream, from the site (race-stats scripts/telemetry/grading_batch.py)."""
    try:
        from staff.app import site_json
        return [(b.get("title") or b["stream"], b["incidents"]) for b in site_json("league/grading.json", ttl=600) or []]
    except Exception:
        return []


def batches():
    """[(batch name, [incident, ...])], newest batch first: stream picks, then the hand-made batches."""
    out = stream_batches()
    for p in sorted(BATCH_DIR.glob("batch-*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append((d.get("title") or p.stem, d["incidents"]))
        except (OSError, ValueError, KeyError):
            continue
    return out


def incident(iid):
    return next((i for _, incs in batches() for i in incs if i["id"] == iid), None)


def read_grades():
    try:
        return json.loads(GRADES_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def fixes():
    try:
        return json.loads((BATCH_DIR / "fixes.json").read_text(encoding="utf-8")).get("fixes", {})
    except (OSError, ValueError):
        return {}


def owner_grade(iid, g):
    """GreMi's grade, with a correction from fixes.json when he has not graded it again since."""
    f = fixes().get(iid)
    if f and (g is None or (not g.get("parts") and int(g.get("at") or 0) <= int(f.get("replaces_at") or 0))):
        return dict(g or {"name": "GreMi"}, **f["grade"], fixed=f.get("why", True))
    return g


def grades_of(iid, grades, owner_id):
    gs = dict(grades.get(iid, {}))
    g = owner_grade(iid, gs.get(str(owner_id)))
    if g:
        gs[str(owner_id)] = g
    return gs


@contextlib.contextmanager
def _locked():
    GRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(str(GRADES_FILE) + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_grades()
        yield data
        tmp = GRADES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        tmp.replace(GRADES_FILE)


def _key(name):
    """'#19 [QDR] Enexvery' -> 'enexvery', to compare with a member's linked driver."""
    name = re.sub(r"^#\d+\s+", "", name or "")
    name = re.sub(r"^\s*\[[^\]]*\]\s*", "", name)
    return re.sub(r"[^a-z0-9]", "", name.lower()).removeprefix("ttv")


def linked_driver(uid):
    try:
        from bot import links as L
        v = L.Links().get(uid)
        return v["driver"] if v else None
    except Exception:
        return None


def involved(inc, driver):
    return bool(driver) and any(_key(d["name"]) == _key(driver) for d in inc["drivers"])


def part_verdict(p):
    if not p.get("real", True):
        return NO
    fault = sorted(d["name"] for d in p["drivers"] if d.get("call") == "fault")
    shared = sorted(d["name"] for d in p["drivers"] if d.get("call") == "shared")
    bits = ([" + ".join(fault) + " at fault"] if fault else []) + ([" + ".join(shared) + " shared"] if shared else [])
    return "; ".join(bits) or RACING


def verdict_of(g):
    parts = g.get("parts") or []
    if len(parts) == 1:
        return part_verdict(parts[0])
    if parts:
        return " | ".join(f"{n}) {part_verdict(p)}" for n, p in enumerate(parts, 1))
    if not g.get("real"):
        return NO
    if not g.get("at_fault"):
        return RACING
    return " + ".join(sorted(g["at_fault"])) + " at fault"


def engine_verdict(inc):
    eng = inc.get("engine") or {}
    if eng.get("at_fault"):
        return " + ".join(sorted(eng["at_fault"])) + " at fault"
    return RACING if eng.get("verdict") not in (NO,) else NO


def _num(v, what):
    try:
        return round(float(str(v).replace(",", ".")), 1)
    except ValueError:
        raise ValueError(f"{what} must be a number of seconds")


def parse_parts(inc, form):
    """The parts of a grade from the form (p<N>_... fields), or None for an old-style form."""
    names = [d["name"] for d in inc["drivers"]]
    idx = sorted({int(m.group(1)) for k in form for m in [re.match(r"p(\d+)_t0$", k)] if m})
    if not idx:
        return None
    parts = []
    for n in idx[:MAX_PARTS]:
        t0, t1 = _num(form.get(f"p{n}_t0"), "from"), _num(form.get(f"p{n}_t1") or form.get(f"p{n}_t0"), "to")
        if t1 < t0:
            t0, t1 = t1, t0
        if form.get(f"p{n}_real") not in ("yes", "no"):
            raise ValueError(f"incident {len(parts) + 1}: say whether it's a real incident")
        real = form.get(f"p{n}_real") == "yes"
        drivers = []
        for k, nm in enumerate(names):
            call = form.get(f"p{n}_call_{k}") or "clear"
            if call not in CALLS:
                call = "clear"
            d = {"name": nm, "call": call}
            sev = (form.get(f"p{n}_sev_{k}") or "").strip()
            if call in ("fault", "shared") and real:
                if not sev:
                    raise ValueError(f"incident {len(parts) + 1}: give {nm} a severity (1-10)")
            if sev and call != "out":
                try:
                    d["severity"] = max(1, min(10, int(sev)))
                except ValueError:
                    raise ValueError("severity must be 1 to 10")
            note = (form.get(f"p{n}_note_{k}") or "").strip()[:300]
            if note:
                d["note"] = note
            drivers.append(d)
        if sum(d["call"] != "out" for d in drivers) < 2:
            raise ValueError(f"incident {len(parts) + 1}: at least two drivers must be involved")
        tags = [t for t in (form.get(f"p{n}_tag") or "").split("\n") if t in TAGS]
        kind = calls.read_type(form, f"p{n}_type") or (None if real else "Not an incident")
        parts.append({"t0": t0, "t1": t1, "real": real, "drivers": drivers, "tags": tags, **({"kind": kind} if kind else {}),
                      "note": (form.get(f"p{n}_pnote") or "").strip()[:500]})
    return sorted(parts, key=lambda p: p["t0"])


def save(inc, uid, name, form):
    parts = parse_parts(inc, form)
    if parts is not None:
        real = any(p["real"] for p in parts)
        fault = sorted({d["name"] for p in parts if p["real"] for d in p["drivers"] if d["call"] in ("fault", "shared")})
        sev = max([d.get("severity") or 0 for p in parts if p["real"] for d in p["drivers"] if d["call"] != "out"] or [0])
        g = {"name": name, "real": real, "at_fault": fault, "severity": sev if real else 0, "parts": parts,
             "note": (form.get("note") or "").strip()[:500], "at": int(time.time())}
        with _locked() as data:
            data.setdefault(inc["id"], {})[str(uid)] = g
        return g
    real = form.get("real") == "yes"
    names = {d["name"] for d in inc["drivers"]}
    fault = sorted({x for x in form.get("fault", "").split("\n") if x in names}) if real else []
    try:
        sev = max(1, min(10, int(form.get("severity") or 0))) if real else 0
    except ValueError:
        raise ValueError("severity must be 1 to 10")
    if form.get("real") not in ("yes", "no"):
        raise ValueError("say whether it's a real incident")
    g = {"name": name, "real": real, "at_fault": fault, "severity": sev,
         "note": (form.get("note") or "").strip()[:500], "at": int(time.time())}
    with _locked() as data:
        data.setdefault(inc["id"], {})[str(uid)] = g
    return g


def summary(inc, grades, owner_id):
    """Consensus for one incident: staff majority, GreMi's grade overrides."""
    gs = grades_of(inc["id"], grades, owner_id)
    staff = {u: g for u, g in gs.items() if u != str(owner_id)}
    votes = collections.Counter(verdict_of(g) for g in staff.values())
    top, n = votes.most_common(1)[0] if votes else (None, 0)
    tie = len([v for v in votes.values() if v == n]) > 1
    owner = gs.get(str(owner_id))
    final = verdict_of(owner) if owner else (None if tie else top)
    sev = [g["severity"] for g in gs.values() if g.get("real")]
    return {"staff": staff, "owner": owner, "votes": votes, "majority": None if tie else top,
            "agree": round(100 * n / len(staff)) if staff else None, "final": final,
            "severity": round(sum(sev) / len(sev), 1) if sev else None,
            "engine": engine_verdict(inc), "engine_ok": None if not final else final == engine_verdict(inc)}


# ---------------------------------------------------------------- pages


def _drivers(inc):
    return ", ".join(d["name"] for d in inc["drivers"])


def _start(i):
    """Where the incident starts on the replay clock (seconds)."""
    if i.get("t0") is not None:
        return float(i["t0"])
    m = re.search(r"inc=([\d.]+)", i.get("replay") or "")
    return float(m.group(1)) if m else 0.0


def default_parts(i, g):
    """The parts to show in the form: the grade's own, an old grade as one part, or one empty part."""
    if g and g.get("parts"):
        return g["parts"]
    t0 = _start(i)
    t1 = float(i.get("t1") or t0 + 15)
    if g:
        return [{"t0": round(t0, 1), "t1": round(t1, 1), "real": g.get("real"), "tags": [], "note": "",
                 "drivers": [{"name": d["name"], "call": "fault" if d["name"] in g.get("at_fault", []) else "clear",
                              **({"severity": g["severity"]} if g.get("real") and d["name"] in g.get("at_fault", []) else {})}
                             for d in i["drivers"]]}]
    return [{"t0": round(t0, 1), "t1": round(t1, 1), "real": None, "tags": [], "note": "", "drivers": []}]


def _clock(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def _part_html(i, n, p, e):
    """One incident (part) of a grade: window, real?, a call + severity + note per driver, reasons."""
    by = {d["name"]: d for d in p.get("drivers") or []}
    rows = []
    for k, d in enumerate(i["drivers"]):
        x = by.get(d["name"], {})
        opts = "".join(f"<option value={c}{' selected' if x.get('call', 'clear') == c else ''}>{e(label)}</option>"
                       for c, label in CALLS.items())
        rows.append(f"<tr><td>{e(d['name'])}<div class=muted style='font-size:12px'>car {e(d.get('car', ''))}</div></td>"
                    f"<td><select name=p{n}_call_{k}>{opts}</select></td>"
                    f"<td><input type=number name=p{n}_sev_{k} min=1 max=10 value='{e(x.get('severity', ''))}' style='width:64px' placeholder='1-10'></td>"
                    f"<td><input name=p{n}_note_{k} maxlength=300 value=\"{e(x.get('note', ''))}\" placeholder='note for this driver' style='width:100%'></td></tr>")
    tags = "".join(f"<label class=tag><input type=checkbox name=p{n}_tag value=\"{e(t)}\"{' checked' if t in (p.get('tags') or []) else ''}> {e(t)}</label>"
                   for t in TAGS)
    real = p.get("real")
    kind = p.get("kind") or i.get("type") or calls.guess_type((i.get("engine") or {}).get("rule"), (i.get("engine") or {}).get("why"))
    guessed = not p.get("kind") and bool(kind)
    return (f"<fieldset class=part data-n={n}><legend><b>Incident <span class=pn>{n + 1}</span></b> "
            f"<button type=button class=link onclick='rmPart(this)'>remove</button></legend>"
            f"<p>From <input type=number step=0.1 name=p{n}_t0 value='{e(p.get('t0', ''))}' required style='width:80px'> to "
            f"<input type=number step=0.1 name=p{n}_t1 value='{e(p.get('t1', ''))}' required style='width:80px'> "
            f"<span class=muted>seconds on the replay clock</span></p>"
            f"<p><b>Real incident?</b> <label><input type=radio name=p{n}_real value=yes required{' checked' if real is True else ''}> Yes</label> "
            f"<label><input type=radio name=p{n}_real value=no{' checked' if real is False else ''}> No, just contact without consequence</label></p>"
            f"<p><b>Type</b> <span>{calls.type_select(e, f'p{n}_type', kind, guessed)}</span></p>"
            f"<div style='overflow-x:auto'><table class=drv><tr><th>Driver</th><th>Call</th><th>Severity</th><th>Note</th></tr>{''.join(rows)}</table></div>"
            f"<p class=muted style='font-size:12px'>Severity: 1 = tiny, 10 = wrecked a race on purpose. Give one to every driver at fault or sharing the blame; "
            f"each driver can get a different one. Shared blame costs half the points.</p>"
            f"<details><summary class=muted style='cursor:pointer'>Why? (reasons, optional)</summary><div>{tags}</div>"
            f"<textarea name=p{n}_pnote maxlength=500 placeholder='What happened in this one' style='min-height:50px'>{e(p.get('note', ''))}</textarea></details>"
            f"</fieldset>")


SCRIPT = """<script>
function addPart(btn){const f=btn.closest('form');const parts=f.querySelectorAll('fieldset.part');
 if(parts.length>=%d)return;const last=parts[parts.length-1];let n=0;parts.forEach(p=>n=Math.max(n,+p.dataset.n+1));
 const c=last.cloneNode(true);c.dataset.n=n;
 c.querySelectorAll('[name]').forEach(el=>{el.name=el.name.replace(/^p\\d+_/,'p'+n+'_');
  if(el.type==='radio'||el.type==='checkbox')el.checked=false;else if(el.tagName!=='SELECT')el.value='';});
 const t1=last.querySelector('[name$=_t1]').value;c.querySelector('[name$=_t0]').value=t1;
 c.querySelector('[name$=_t1]').value=t1?(+t1+5).toFixed(1):'';
 c.querySelectorAll('select').forEach(s=>s.value='clear');last.after(c);renum(f);}
function rmPart(b){const f=b.closest('form');if(f.querySelectorAll('fieldset.part').length>1){b.closest('fieldset').remove();renum(f);}}
function renum(f){f.querySelectorAll('fieldset.part .pn').forEach((s,i)=>s.textContent=i+1);}
</script>""" % MAX_PARTS

STYLE = ("<style>.btn{display:inline-block;background:#3f3f46;color:#fff;padding:6px 12px;border-radius:8px}"
         "details[open] summary{margin-bottom:8px}fieldset.part{border:1px solid #3f3f46;border-radius:10px;margin:10px 0;padding:8px 12px}"
         "table.drv td,table.drv th{padding:4px 6px;vertical-align:middle}table.drv{width:100%}"
         "label.tag{display:inline-block;margin:2px 10px 2px 0;font-size:13px}"
         "button.link{background:none;border:0;color:#a1a1aa;text-decoration:underline;cursor:pointer;padding:0 4px;font-size:12px}"
         ".tl{font-size:13px;margin:4px 0 8px}</style>")


def _timeline(i, e):
    cs = i.get("contacts") or []
    if not cs:
        return ""
    nm = {d["car"]: d["name"] for d in i["drivers"]}
    return ("<details class=tl><summary class=muted style='cursor:pointer'>Contacts the telemetry saw (" + str(len(cs)) + ")</summary>"
            + "".join(f"<div>{_clock(c['t'])} ({c['t']:.1f} s): {e(nm.get(c['a'], 'car %s' % c['a']))} ↔ {e(nm.get(c['b'], 'car %s' % c['b']))}</div>" for c in cs)
            + "</details>")


def replay(i):
    """The race viewer link of a graded incident; links made before the game scopes start with /f1/."""
    return (i.get("replay") or "").replace("/race-stats/f1/", "/race-stats/all/")


def viewer(i):
    """The incident viewer at a graded incident (GreMi, 6 Nov 2026: every staff link opens it)."""
    from staff import races
    names = [re.sub(r"^#\d+\s+", "", d["name"]) for d in i.get("drivers") or []]
    return races.viewer_url(i["race_file"], i.get("lap"), names) if i.get("race_file") else replay(i)


def grading_page(user, q, h):
    """h: helpers from app.py (e, page, csrf_token, is_owner)."""
    e, uid = h["e"], int(user["id"])
    owner = h["is_owner"](uid)
    grades = read_grades()
    me = linked_driver(uid)
    body = ["<h1>Incident grading</h1><p class=muted>Watch each replay, then give your call. "
            "You don't see the fault engine's call or other staff grades, so your view stays your own. "
            "Incidents you drove in are hidden for you. You can change a grade any time.</p>"
            "<p class=muted>More than one thing happened? Press <b>Split: add another incident</b> and give each one its own "
            "time window, drivers, call and severity per driver. Mark drivers who had nothing to do with it as <i>Not involved</i>.</p>"]
    if owner:
        body.append("<p><a href='/grading/results'>See all staff grades and the engine comparison →</a></p>")
    body.append("<p class=muted>The <b>Type</b> is filled in with the engine's guess: change it if it's wrong, or pick "
                "<i>+ new type</i> to add one. <a href='/grading/learned'>What the engine learned from your grades →</a></p>")
    prot = h.get("protests", lambda u: "")(user)
    if prot:
        body.append("<h2>Protests · grade these first <span class=muted>· drivers who don't agree with a call</span></h2>" + prot)
    want = q.get("id", [""])[0]
    for title, incs in batches():
        mine = [i for i in incs if not involved(i, me)]
        done = sum(1 for i in mine if str(uid) in grades.get(i["id"], {}))
        body.append(f"<h2>{e(title)} <span class=muted>· you graded {done} of {len(mine)}</span></h2>")
        for i in mine:
            g = grades.get(i["id"], {}).get(str(uid))
            if owner:
                g = owner_grade(i["id"], g)
            open_ = " open" if (want == i["id"] or (not want and not g and done < len(mine) and i is next(
                (x for x in mine if str(uid) not in grades.get(x["id"], {})), None))) else ""
            state = f"<span class=pos>✓ {e(verdict_of(g))}" + (f", severity {g['severity']}" if g.get("real") and not g.get("parts") else "") + "</span>" if g else "<span class=muted>not graded</span>"
            if g and g.get("fixed"):
                state += f" <span class=muted>(corrected: {e(g['fixed'] if isinstance(g['fixed'], str) else 'split from your note')})</span>"
            parts = "".join(_part_html(i, n, p, e) for n, p in enumerate(default_parts(i, g)))
            body.append(
                f"<details class=card id='{e(i['id'])}'{open_}><summary style='cursor:pointer'>"
                f"<b>{e(i['id'].upper())}</b> · {e(i['track'])} · {e(i['date'])} · lap {e(i['lap'])} · {e(_drivers(i))}"
                f"{calls.weather_badge(e, i.get('weather'))}"
                f"<br>{state}</summary>"
                + (f"<details style='margin:0 0 10px'><summary class=muted style='cursor:pointer'>Claude's take "
                   f"({e(PICKS.get(i.get('pick'), 'pick'))}), only you see this; open it after grading</summary>"
                   f"<p class=quote>{e(i['claude'])}</p></details>" if owner and i.get("claude") else "")
                + f"<p><a class=btn href=\"{e(viewer(i))}\" target=_blank rel=noopener>▶ Incident viewer</a> "
                f"<a href=\"{e(replay(i))}\" target=_blank rel=noopener class=muted>full race replay</a> "
                f"<span class=muted>({e(i.get('race_time', ''))} race time)</span></p>"
                + _timeline(i, e)
                + f"<form method=post action='/grade'>"
                f"<input type=hidden name=csrf value='{e(h['csrf_token'](user))}'>"
                f"<input type=hidden name=id value='{e(i['id'])}'>"
                + parts
                + "<p><button type=button class=btn onclick='addPart(this)'>＋ Split: add another incident</button></p>"
                f"<p><textarea name=note maxlength=500 placeholder='Anything else? (optional, helps us tune the rules)' style='min-height:50px'>{e((g or {}).get('note', ''))}</textarea></p>"
                f"<button>{'Update grade' if g else 'Save grade'}</button></form></details>")
    if len(body) == 1 or not batches():
        body.append("<p>No incidents to grade right now.</p>")
    return h["page"]("Grading", STYLE + SCRIPT + "".join(body), user, "grading")


def grade_html(g, e):
    """A grade for the results page: the call, then per part the drivers' severities and notes."""
    out = e(verdict_of(g)) + (f" · sev {g['severity']}" if g.get("real") and not g.get("parts") else "")
    for n, p in enumerate(g.get("parts") or [], 1):
        drv = "; ".join(f"{e(d['name'])}: {e(CALLS.get(d['call'], d['call']).lower())}"
                        + (f" {d['severity']}/10" if d.get("severity") and d["call"] in ("fault", "shared") else "")
                        + (f" <i>({e(d['note'])})</i>" if d.get("note") else "")
                        for d in p["drivers"] if d["call"] != "out")
        out += (f"<div class=muted style='font-size:12px'>{n}) {p['t0']}–{p['t1']} s: {drv}"
                + (f" · {e(', '.join(p['tags']))}" if p.get("tags") else "") + (f" · {e(p['note'])}" if p.get("note") else "") + "</div>")
    if g.get("note"):
        out += f"<div class=muted>{e(g['note'])}</div>"
    if g.get("fixed"):
        out += "<div class=muted style='font-size:12px'>corrected outside the page (fixes.json)</div>"
    return out


def results_page(user, q, h):
    e = h["e"]
    if not h["is_owner"](int(user["id"])):
        return h["page"]("Grading results", "<p>Only the owner can see all grades.</p>", user, "grading")
    owner_id = int(user["id"])
    grades = read_grades()
    out = ["<h1>Grading results</h1><p class=muted>Your grade is final; without it the staff majority counts "
           "(a tie stays open). Engine ✓ = the fault engine agrees with the final call. "
           "<a href='/grading/export.json'>Download all as JSON</a></p>"]
    graders = collections.defaultdict(lambda: [0, 0, 0])  # done, compared, agreed with final
    for title, incs in batches():
        rows, ok, decided = [], 0, 0
        for i in incs:
            s = summary(i, grades, owner_id)
            if s["engine_ok"] is not None:
                decided += 1
                ok += s["engine_ok"]
            for u, g in s["staff"].items():
                graders[g["name"]][0] += 1
                if s["final"] and (s["owner"] or s["majority"]):
                    graders[g["name"]][1] += 1
                    graders[g["name"]][2] += verdict_of(g) == s["final"]
            staff = "".join(f"<div><b>{e(g['name'])}</b>: {grade_html(g, e)}</div>"
                            for g in sorted(s["staff"].values(), key=lambda g: g["name"].lower())) or "<span class=muted>none yet</span>"
            mine = grade_html(s["owner"], e) if s["owner"] else f"<a href='/grading?id={e(i['id'])}#{e(i['id'])}'>grade it</a>"
            eng = i.get("engine") or {}
            mark = "" if s["engine_ok"] is None else ("<span class=pos> ✓</span>" if s["engine_ok"] else "<span class=neg> ✗</span>")
            rows.append(
                f"<tr><td><b>{e(i['id'].upper())}</b><br><a href=\"{e(viewer(i))}\" target=_blank rel=noopener>incident viewer</a>"
                f"<div class=muted>{e(i['track'])} {e(i['date'])} L{e(i['lap'])}</div></td>"
                f"<td>{e(_drivers(i))}</td><td>{staff}</td>"
                f"<td>{e(s['majority'] or ('tie' if s['votes'] else '–'))}"
                + (f"<div class=muted>{s['agree']}% of {len(s['staff'])} agree</div>" if s["agree"] is not None else "")
                + f"</td><td>{mine}</td>"
                f"<td>{e(s['engine'])}{mark}<div class=muted>{e(eng.get('rule', ''))} · sev {e(eng.get('severity', '–'))}<br>{e(eng.get('why', ''))}</div>"
                + (f"<details><summary class=muted style='cursor:pointer'>Claude's take · {e(PICKS.get(i.get('pick'), ''))}</summary>"
                   f"<div class=muted>{e(i['claude'])}</div></details>" if i.get("claude") else "") + "</td></tr>")
        head = f"<h2>{e(title)}" + (f" <span class=muted>· engine agrees on {ok} of {decided} decided</span>" if decided else "") + "</h2>"
        out.append(head + "<div class=card style='overflow-x:auto'><table><tr><th>Incident</th><th>Drivers</th><th>Staff grades</th>"
                   "<th>Staff majority</th><th>Your grade</th><th>Fault engine</th></tr>" + "".join(rows) + "</table></div>")
    if graders:
        out.insert(1, "<div class=card><b>Graders</b><table><tr><th>Staff</th><th>Graded</th><th>Agree with final call</th></tr>"
                   + "".join(f"<tr><td>{e(n)}</td><td>{d}</td><td>{f'{round(100 * a / c)}% of {c}' if c else '–'}</td></tr>"
                             for n, (d, c, a) in sorted(graders.items(), key=lambda x: -x[1][0])) + "</table></div>")
    return h["page"]("Grading results", "".join(out), user, "grading")


def decisions(grades, owner_id):
    """The final call on every graded incident, for the fault engine's precedents (race-stats
    grading/decisions.json): GreMi's grade when he gave one, else the staff majority. No staff
    names or Discord ids; GreMi's note is kept because it says why."""
    out = {}
    for _, incs in batches():
        for i in incs:
            s = summary(i, grades, owner_id)
            if not s["final"] or i["id"] in out:
                continue
            o = s["owner"]
            if o:
                d = {"by": "GreMi", "real": o["real"], "at_fault": o["at_fault"], "severity": o["severity"],
                     "note": o.get("note", ""), "at": o.get("at")}
                g = o
            else:
                g = next(g for g in s["staff"].values() if verdict_of(g) == s["final"])
                d = {"by": "staff", "real": g["real"], "at_fault": g["at_fault"], "severity": s["severity"] or 0,
                     "note": "", "votes": len(s["staff"]), "agree": s["agree"]}
            if g.get("parts"):
                d["parts"] = decision_parts(i, g["parts"], o is not None)
                d["kind"] = next((p["kind"] for p in g["parts"] if p.get("real") and p.get("kind")), None) or g["parts"][0].get("kind")
            out[i["id"]] = {"verdict": s["final"], **d, "drivers": i["drivers"], "track": i.get("track"),
                            "date": i.get("date"), "race_file": i.get("race_file"), "recording": i.get("recording"),
                            "replay": i.get("replay"), "engine": i.get("engine")}
    return out


def decision_parts(i, parts, notes):
    """Parts for the fault engine: car numbers, the call and severity per driver (notes only from GreMi)."""
    car = {d["name"]: d["car"] for d in i["drivers"]}
    out = []
    for p in parts:
        out.append({"t0": p["t0"], "t1": p["t1"], "real": p["real"], "verdict": part_verdict(p), "tags": p.get("tags") or [],
                    **({"kind": p["kind"]} if p.get("kind") else {}),
                    "note": p.get("note", "") if notes else "",
                    "drivers": [{"car": car[d["name"]], "name": d["name"], "call": d["call"],
                                 **({"severity": d["severity"]} if d.get("severity") else {}),
                                 **({"note": d["note"]} if notes and d.get("note") else {})}
                                for d in p["drivers"] if d["name"] in car]})
    return out


DECISIONS = "grading/decisions.json"
_sync_lock = threading.Lock()


def sync_decisions(owner_id, gh_file, save_json):
    """Commits the final calls to race-stats when they changed (the site build reads them)."""
    with _sync_lock:
        new = decisions(read_grades(), owner_id)
        data, _ = gh_file(DECISIONS)
        if data is not None and data.get("decisions") == new:
            return False

        def edit(d):
            d.clear()
            d.update({"about": "Final calls on graded incidents (GreMi's grade, else the staff majority). "
                               "Written by the staff page; the fault engine uses them as precedents.",
                      "decisions": new})
        save_json(DECISIONS, edit, f"Graded incidents: {len(new)} final calls", {})
        return True


def export(owner_id):
    grades = read_grades()
    out = []
    for title, incs in batches():
        for i in incs:
            s = summary(i, grades, owner_id)
            out.append({"batch": title, **{k: i[k] for k in ("id", "track", "date", "lap", "race_time", "replay", "drivers")},
                        "pick": i.get("pick"), "claude": i.get("claude"),
                        "engine": i.get("engine"), "final": s["final"], "majority": s["majority"], "agree": s["agree"],
                        "avg_severity": s["severity"], "owner": s["owner"],
                        "staff": list(s["staff"].values())})
    return json.dumps(out, indent=1, ensure_ascii=False)
