"""Structured calls for the staff page (GreMi, 5 Nov 2026).

Changing a penalty or answering a protest is done like grading: pick the incident type, who was to
blame (at fault / shared / not at fault) and the severity, and the points follow from the same
table the safety rating uses. A manual points override stays for special cases. The call is
saved with the change, so the fault engine (race-stats scripts/telemetry/precedents.py) learns
the type and severity too, not only "the points changed".

Also here: the incident types (a fixed list plus the ones staff add), the driver summaries page
(league/driver-profiles.json, staff only, never used for a call) and the "What I learned" page
(league/learned.json).
"""
import json
import os
import pathlib
import re
import threading

TYPES = ["Rear-end", "Moved across alongside", "Late lunge / divebomb", "Unsafe rejoin", "Blocking / brake-check",
         "Retaliation", "Lost control", "Chain reaction", "Racing incident", "Not an incident"]
TYPES_FILE = pathlib.Path(os.environ.get("STAFF_TYPES_FILE", "/var/lib/race-control/incident-types.json"))
FAULTS = {"fault": "At fault", "shared": "Shared blame (half points)", "clear": "Not at fault (no points)"}
NEW = "__new"
R9_PTS = 20.0   # v2.2 scale: hitting the same car again on purpose
_lock = threading.Lock()


# ---------------------------------------------------------------- incident types


def custom_types():
    try:
        return [t for t in json.loads(TYPES_FILE.read_text(encoding="utf-8")) if isinstance(t, str)]
    except (OSError, ValueError):
        return []


def types():
    return TYPES + [t for t in custom_types() if t not in TYPES]


def clean_type(t):
    return re.sub(r"\s+", " ", (t or "").strip())[:40]


def add_type(t):
    """Keeps a type staff typed themselves, so it shows in every picker from now on."""
    t = clean_type(t)
    if not t or t.lower() in {x.lower() for x in types()}:
        return next((x for x in types() if x.lower() == t.lower()), t)
    with _lock:
        have = custom_types()
        have.append(t)
        TYPES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TYPES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(have, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(TYPES_FILE)
    return t


def read_type(form, name):
    """The type picked in a form (a new one is added to the list), or None."""
    t = form.get(name) or ""
    if t == NEW:
        t = form.get(name + "_new") or ""
        return add_type(t) if clean_type(t) else None
    t = clean_type(t)
    return t or None


def guess_type(rule=None, why=""):
    """The type the engine's call points at, when the data has no type of its own."""
    why = why or ""
    if rule == "R1" or "Not an incident" in why:
        return "Not an incident"
    if rule == "R5" or "came back onto the track" in why:
        return "Unsafe rejoin"
    if rule == "R9" or "hit" in why and "again" in why:
        return "Retaliation"
    if rule == "R4" or "chain of" in why:
        return "Chain reaction"
    if rule == "R7" or "no time to react" in why or "aftermath" in why:
        return "Chain reaction" if "aftermath" in why else "Racing incident"
    if "swept" in why:
        return "Blocking / brake-check"
    if rule in ("R2", "R8") or "side by side" in why:
        return "Moved across alongside"
    if rule == "R3" or "ran into the back" in why:
        return "Rear-end"
    return None


# ---------------------------------------------------------------- points


def points(kind, fault, sev):
    """Points on the v2.2 scale the staff page types (the safety rating converts them), as a penalty (minus)."""
    if fault == "clear" or kind == "Not an incident":
        return 0.0
    sev = max(1, min(10, int(sev or 1)))
    pts = R9_PTS if kind == "Retaliation" and sev >= 10 else 1.0 if sev <= 3 else 3.0 if sev <= 5 else 6.0 if sev <= 7 else 10.0
    return -(pts / 2 if fault == "shared" else pts)


def severity_in(text):
    m = re.search(r"severity (\d+)/10", text or "")
    return int(m.group(1)) if m else None


def read_call(form, lo=-50, hi=10):
    """(call, points) from a picker: {kind, fault, severity}; the override box wins when ticked."""
    fault = form.get("fault") if form.get("fault") in FAULTS else None
    if not fault:
        raise ValueError("Say who was to blame.")
    kind = read_type(form, "kind")
    if not kind:
        raise ValueError("Pick the incident type (or type a new one).")
    try:
        sev = max(1, min(10, int(form.get("sev") or 0))) if fault != "clear" else int(form.get("sev") or 0) or None
    except ValueError:
        raise ValueError("Severity must be 1 to 10.")
    if fault != "clear" and not sev:
        raise ValueError("Give a severity (1-10).")
    call = {"kind": kind, "fault": fault, **({"severity": sev} if sev else {})}
    pts = points(kind, fault, sev)
    if form.get("override") == "1":
        try:
            pts = float(str(form.get("pts")).replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError("The override points must be a number.")
        if not lo <= pts <= hi:
            raise ValueError(f"Points must be between {lo} and {hi}.")
        call["override"] = True
    return call, pts


# ---------------------------------------------------------------- html


def type_select(e, name, current, guessed=False):
    opts = list(types())
    if current and current not in opts:
        opts.append(current)
    html = "".join(f"<option{' selected' if t == current else ''}>{e(t)}</option>" for t in opts)
    return (f"<select name={name} class=kind onchange=\"kindNew(this)\" required>"
            + ("" if current else "<option value='' selected disabled>pick a type</option>")
            + html + f"<option value={NEW}>+ new type…</option></select>"
            f"<input name={name}_new maxlength=40 placeholder='new type' style='display:none;width:140px'>"
            + (" <span class=muted title='The fault engine guessed this from its call. Change it if it is wrong.'>(guessed)</span>" if guessed else ""))


def picker(e, kind=None, fault="fault", sev=None, pts=None, guessed=False, lo=-50, hi=10):
    """Type + blame + severity, the points they give, and the manual override."""
    fopts = "".join(f"<option value={k}{' selected' if k == fault else ''}>{e(v)}</option>" for k, v in FAULTS.items())
    return (f"<div class=picker><label>Type {type_select(e, 'kind', kind, guessed)}</label> "
            f"<label>Call <select name=fault>{fopts}</select></label> "
            f"<label>Severity <input type=number name=sev min=1 max=10 value='{e(sev or '')}' placeholder='1-10'></label> "
            f"<span class='mono calc'></span>"
            f"<label class=muted style='font-size:12px'><input type=checkbox name=override value=1 onchange='calc(this)'> override points</label>"
            f"<input type=number name=pts step=0.5 min={lo} max={hi} value='{e(pts if pts is not None else 0)}' class=ovr style='display:none' title='Points (v2.2 scale)'>"
            f"</div>")


SCRIPT = """<script>
function kindNew(s){const i=s.parentNode.querySelector('input[name$=_new]');if(!i)return;i.style.display=s.value==='__new'?'':'none';
 i.required=s.value==='__new';calc(s);}
function pts(kind,fault,sev){if(fault==='clear'||kind==='Not an incident')return 0;sev=Math.max(1,Math.min(10,+sev||1));
 let p=(kind==='Retaliation'&&sev>=10)?20:sev<=3?1:sev<=5?3:sev<=7?6:10;return -(fault==='shared'?p/2:p);}
function calc(el){const f=el.closest('.picker');if(!f)return;const o=f.querySelector('[name=override]').checked;
 f.querySelector('.ovr').style.display=o?'':'none';
 const p=pts(f.querySelector('[name=kind]').value,f.querySelector('[name=fault]').value,f.querySelector('[name=sev]').value);
 f.querySelector('.calc').textContent=o?'':'= '+(p>0?'+':'')+p+' pts';}
document.addEventListener('input',ev=>{if(ev.target.closest&&ev.target.closest('.picker'))calc(ev.target);});
document.addEventListener('change',ev=>{if(ev.target.closest&&ev.target.closest('.picker'))calc(ev.target);});
document.addEventListener('DOMContentLoaded',()=>document.querySelectorAll('.picker [name=sev]').forEach(calc));
</script>"""

STYLE = ("<style>.btn{display:inline-block;background:#3f3f46;color:#fff;padding:6px 12px;border-radius:8px}.picker{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}.picker label{white-space:nowrap}"
         ".wx{display:inline-block;font-size:11px;padding:1px 6px;border-radius:99px;background:#27272a;color:#d4d4d8;margin-left:6px}"
         ".wx.wet{background:#1e3a8a;color:#bfdbfe}.prof{font-size:13px;margin:6px 0}.prof li{margin:2px 0}"
         ".stat{display:inline-block;margin:0 14px 6px 0}.stat b{display:block;font-size:16px}</style>")


def weather_badge(e, w):
    if not w:
        return ""
    wet = w in ("Light rain", "Heavy rain", "Storm")
    return f"<span class='wx{' wet' if wet else ''}' title='Weather at the incident{'; wet: racing incident a bit sooner' if wet else ''}'>{'🌧' if wet else '☀'} {e(w)}</span>"


# ---------------------------------------------------------------- driver summaries


def _key(n):
    n = re.sub(r"^#\d+\s+", "", n or "")
    n = re.sub(r"^\s*\[[^\]]*\]\s*", "", n)
    return re.sub(r"[^a-z0-9]", "", n.lower()).removeprefix("ttv")


def profiles():
    try:
        from staff.app import site_json
        d = site_json("league/driver-profiles.json", ttl=600) or {}
    except Exception:
        d = {}
    return d


def profile_of(name, data=None):
    data = data if data is not None else profiles()
    k = _key(name)
    return next((p for p in data.get("drivers") or [] if _key(p["name"]) == k), None)


def _pct(part, whole):
    return f"{round(100 * part / whole)}%" if whole else "–"


def profile_html(e, p, short=False):
    if not p:
        return ""
    ph, tot = p.get("phases") or {}, sum((p.get("phases") or {}).values())
    how = ", ".join(f"{_pct(v, tot)} {k}" for k, v in ph.items()) if tot else "no at-fault contacts"
    notes = "".join(f"<li>{e(x)}</li>" for x in p.get("notes") or [])
    if short:
        return (f"<div class=prof><b>{e(p['name'])}</b> <span class=muted>{p['races']} races · at fault {p['fault_per_race']:g}/race · "
                f"hit {p['hit_per_race']:g}/race · when at fault: {e(how)}</span>" + (f"<ul>{notes}</ul>" if notes else "") + "</div>")
    types = ", ".join(f"{e(k)} {v}" for k, v in (p.get("types") or {}).items()) or "–"
    stats = [("Races", p["races"]), ("Avg finish", p.get("finish") or "–"), ("Places gained", p.get("gained") if p.get("gained") is not None else "–"),
             ("Best lap off fastest", f"{p['pace_gap_pct']:g}%" if p.get("pace_gap_pct") is not None else "–"),
             ("Lap spread", f"{p['spread_pct']:g}%" if p.get("spread_pct") is not None else "–"),
             ("At fault / race", p["fault_per_race"]), ("Hit by others / race", p["hit_per_race"]),
             ("Avg severity (at fault)", p.get("avg_severity") or "–"),
             ("Side by side / race", f"{p['side_s_per_race']:g} s"), ("Battles / race", p["battles_per_race"]),
             ("Battles without contact", f"{p['clean_battle_pct']}%" if p.get("clean_battle_pct") is not None else "–"),
             ("Passes in battles / race", p["passes_per_race"]), ("Lash-outs (R9)", p["lash_outs"]),
             ("At fault soon after being hit", p["after_hit"])]
    t = p.get("trend") or {}
    return (f"<div class=card id='d-{e(_key(p['name']))}'><h2 style='margin-top:0'>{e(p['name'])}</h2>"
            + "".join(f"<span class=stat><span class=muted>{e(k)}</span><b>{e(v)}</b></span>" for k, v in stats)
            + f"<p><b>How they hit others when at fault:</b> {e(how)}<br><b>Incident types:</b> {types}"
            + (f"<br><b>Trend:</b> {t['recent']:g} at fault per race in the last 5 races"
               + (f", {t['before']:g} before" if t.get("before") is not None else "") if t else "")
            + (f"<br><span class=muted>{p['wet_races']} wet races</span>" if p.get("wet_races") else "") + "</p>"
            + (f"<b>Notes</b><ul class=prof>{notes}</ul>" if notes else "") + "</div>")


def drivers_page(user, q, h):
    e = h["e"]
    d = profiles()
    ps = d.get("drivers") or []
    lg = d.get("league") or {}
    want = q.get("name", [""])[0]
    if want:
        ps = [p for p in ps if _key(p["name"]) == _key(want)] or ps
    head = ("<h1>Driver summaries</h1><p class=muted>Every driver with "
            f"{e(d.get('min_races', 5))}+ judged races (from 19 Oct 2026 on). For tips and context only: the fault engine and the "
            "safety rating never look at this, so a driver's history doesn't change the call on an incident. "
            "'When at fault' is from the pedals, steering and sideways movement just before each contact.</p>"
            + (f"<p class=muted>League average: at fault {lg.get('fault_per_race')}/race, hit {lg.get('hit_per_race')}/race, "
               f"best lap {lg.get('pace_gap_pct')}% off the fastest, {lg.get('clean_battle_pct')}% of battles without contact. "
               f"Updated {e(d.get('made', ''))}.</p>" if lg else ""))
    idx = "<div class=card>" + " · ".join(f"<a href='#d-{e(_key(p['name']))}'>{e(p['name'])}</a>" for p in ps) + "</div>" if len(ps) > 1 else ""
    body = "".join(profile_html(e, p) for p in ps) or "<div class='card muted'>No summaries yet: they come with the next site build.</div>"
    return h["page"]("Drivers", STYLE + head + idx + body, user, "drivers")


# ---------------------------------------------------------------- what was learned


def learned_page(user, q, h):
    e = h["e"]
    try:
        from staff.app import site_json
        d = site_json("league/learned.json", ttl=600) or {}
    except Exception:
        d = {}
    if not d:
        return h["page"]("What I learned", "<h1>What I learned</h1><div class='card muted'>Nothing yet: it comes with the next site build.</div>",
                         user, "learned")
    g = d.get("graded") or {}
    out = ["<h1>What I learned from grading</h1><p class=muted>Every grade, accepted or rejected protest and staff change "
           "with 'engine got it wrong' teaches the fault engine. This page shows what changed in the rules, what the grades "
           f"taught it beyond the rules, and what that changes in today's calls. Updated {e(d.get('made', ''))}.</p>"]
    src = g.get("source") or {}
    out.append(f"<div class=card><span class=stat><span class=muted>Graded incidents</span><b>{g.get('incidents', 0)}</b></span>"
               + "".join(f"<span class=stat><span class=muted>{e(k)}</span><b>{v}</b></span>" for k, v in (g.get("by") or {}).items())
               + "".join(f"<span class=stat><span class=muted>{e(k)}</span><b>{v}</b></span>" for k, v in src.items() if k != "grade")
               + f"<span class=stat><span class=muted>Contacts the rulebook already got right</span><b>{g.get('contacts_agree', 0)}</b></span>"
               f"<span class=stat><span class=muted>Contacts the grades corrected</span><b>{g.get('contacts_differ', 0)}</b></span>"
               + (f"<div class=muted>Types graded: {e(', '.join(f'{k} {v}' for k, v in g['types'].items()))}</div>" if g.get("types") else "")
               + "</div>")
    acc = d.get("accuracy") or []
    if acc:
        rows = "".join(f"<tr><td>{e(a['date'])}</td><td>{e(a.get('rules', ''))}</td><td>{a['incidents']}</td>"
                       f"<td>{a['inc_rulebook']} ({_pct(a['inc_rulebook'], a['incidents'])})</td>"
                       f"<td><b>{a['inc_with_precedents']} ({_pct(a['inc_with_precedents'], a['incidents'])})</b></td></tr>" for a in reversed(acc))
        out.append("<h2>How often I get it right</h2><div class=card><p class=muted>Each graded incident is judged as if it was never "
                   "graded (leave one out): first with the rulebook only, then with everything else that was graded.</p>"
                   "<table><tr><th>Date</th><th>Rulebook</th><th>Graded incidents</th><th>Rulebook alone</th><th>With grades</th></tr>"
                   + rows + "</table></div>")
    ch = d.get("changes") or {}
    if ch:
        c = ch.get("counts") or {}
        confirmed = f", and {c['confirmed']} the grades confirmed" if c.get("confirmed") else ""
        rows = "".join(f"<tr><td>{e(r['date'])} {e(r['track'])}{' L' + e(r['lap']) if r.get('lap') else ''}</td>"
                       f"<td>{e(r['what'])}{' (graded itself)' if r.get('graded_itself') else ' (like ' + e((r.get('follows') or '').upper()) + ')' if r.get('follows') else ''}</td>"
                       f"<td class=muted>{e(r.get('before') or '')}</td><td>{e(r.get('after') or '')}</td></tr>" for r in ch.get("recent") or [])
        out.append(f"<h2>What it changes in the calls now</h2><div class=card><p>{ch.get('total', 0)} incident calls are different from what "
                   f"the rulebook alone would say: {c.get('cleared', 0)} cleared, {c.get('blamed', 0)} blamed, {c.get('moved', 0)} blame moved"
                   f"{confirmed}.</p>"
                   + (f"<table><tr><th>Incident</th><th>Change</th><th>Rulebook said</th><th>Now</th></tr>{rows}</table>" if rows else "") + "</div>")
    ls = d.get("lessons") or []
    if ls:
        out.append("<h2>Lessons from the grades</h2><div class=card><p class=muted>Graded contacts where the final call is different "
                   "from the rulebook, grouped by situation. Similar new contacts follow these when the closest graded ones agree.</p><ol>"
                   + "".join(f"<li style='margin:6px 0'>{e(x['text'])}<div class=muted>"
                             + ", ".join(f"{e(s['id'].upper())} {e(s.get('track') or '')} {e(s.get('date') or '')}"
                                         + (f" ({e(s['kind'])})" if s.get("kind") else "") for s in x.get("examples") or [])
                             + "</div></li>" for x in ls) + "</ol></div>")
    rb = d.get("rulebook") or []
    if rb:
        out.append("<h2>Rule changes</h2>" + "".join(
            f"<div class=card><b>{e(r['date'])} · {e(r['version'])}</b> <span class=muted>from {e(r.get('from', ''))}</span><ul>"
            + "".join(f"<li>{e(x)}</li>" for x in r.get("changes") or []) + "</ul></div>" for r in rb))
    return h["page"]("What I learned", STYLE + "".join(out), user, "learned")
