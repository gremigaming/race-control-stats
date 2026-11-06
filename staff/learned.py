"""The staff page's "What I learned" (GreMi, 6 Nov 2026): every lesson the fault engine took, as a
log with a picture.

Reads league/learned.json (race-stats scripts/telemetry/learned.py): one entry per lesson with when,
who, the incident, the call and why, a top-down sketch with the pedals, the calls it changed and
whether it still matters, plus graded incidents that look alike but got a different call. Notes and
reasons come from the race-stats repo (grading/decisions*.json, races/corrections.json), which the
public site file leaves out. Staff only.

Sections: Lessons (newest first; outdated ones folded), Calls changed by lessons, Look alike,
different call (with "change this call" links and "they are different"), Rule changes, Accuracy.
"""
import itertools
import json
import math
import os
import pathlib
import threading
import time
import urllib.parse

DISTINCT_FILE = pathlib.Path(os.environ.get("STAFF_DISTINCT_FILE", "/var/lib/race-control/not-similar.json"))
_lock = threading.Lock()
_notes = {"at": 0, "data": None}
_ids = itertools.count()
COLORS = ["#f43f5e", "#22d3ee", "#a78bfa", "#fbbf24"]   # at fault first; others cyan, violet, amber
SOURCE = {"grade": "Grade", "protest accepted": "Protest accepted", "protest rejected": "Protest not accepted",
          "staff change": "Staff change"}
FEATURE_NAMES = {"gap": ("apart along the track", "m"), "closing": ("closing speed", "km/h"),
                 "moved_front": ("car ahead moved across", "m"), "moved_back": ("car behind moved across", "m"),
                 "rejoin_front": ("car ahead rejoining", ""), "rejoin_back": ("car behind rejoining", ""),
                 "slide": ("biggest slide", "°"), "lost": ("places lost", ""), "off": ("cars off track", ""),
                 "cars": ("cars in it", ""), "reaction": ("no time to react", ""), "aftermath": ("aftermath", "")}

STYLE = """<style>
.lg-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}.lg-tabs a{padding:6px 12px;border-radius:12px;background:rgba(255,255,255,.04);color:#d4d4d8;font-size:13px}
.lg-tabs a:hover{background:rgba(244,63,94,.12);color:#fff}
.stats{display:flex;gap:10px;flex-wrap:wrap}.stats .st{flex:1;min-width:130px;padding:12px 14px;border-radius:14px;background:rgba(255,255,255,.03);box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}
.stats .st b{display:block;font-size:22px;color:#fff;font-weight:650}.stats .st span{font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#71717a;font-family:var(--mono)}
.lesson{display:grid;grid-template-columns:230px 1fr;gap:16px;align-items:start}@media(max-width:700px){.lesson{grid-template-columns:1fr}}
.lesson .sk{background:#0b0a0e;border-radius:12px;overflow:hidden;box-shadow:inset 0 0 0 1px rgba(255,255,255,.06)}
.lesson .sk svg{display:block;width:100%;height:auto}
.lesson h3{margin:2px 0 6px;font-size:15px}.lesson .meta{font-size:12px;color:#a1a1aa}.lesson .call{margin:6px 0}
.lesson .why{margin:6px 0;color:#e4e4e7}.lesson .ins{font-size:12px;color:#a1a1aa;margin:4px 0}.lesson .ins i{font-style:normal;display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:4px;vertical-align:-1px}
.src{display:inline-block;font-size:11px;padding:1px 8px;border-radius:99px;background:rgba(124,58,237,.18);color:#ddd6fe;margin-left:6px}
.src.protest{background:rgba(244,63,94,.16);color:#fecdd3}.src.staff{background:rgba(34,211,238,.12);color:#a5f3fc}
.st-confirmed{opacity:.8}.st-outdated{opacity:.6}
.chg{font-size:12px;margin:6px 0 0;padding:0;list-style:none}.chg li{margin:3px 0}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:700px){.pair{grid-template-columns:1fr}}
.pair .side{background:rgba(255,255,255,.02);border-radius:12px;padding:10px}
.nums td{font-size:12px;padding:3px 6px}.nums td.hl{color:#fda4af}
.lg-filter{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.lg-filter button{background:rgba(255,255,255,.05);border-radius:99px;padding:4px 12px}
.lg-filter button.on{background:linear-gradient(90deg,var(--brand-red),var(--brand-violet))}
</style>"""

SCRIPT = """<script>
function lgFilter(btn, what){
  document.querySelectorAll('.lg-filter button').forEach(b=>b.classList.toggle('on', b===btn));
  document.querySelectorAll('#lessons .card[data-src]').forEach(c=>{
    const ok = what==='all' || (what==='changed' ? c.dataset.changed==='1' : c.dataset.src===what || c.dataset.by===what);
    c.style.display = ok ? '' : 'none';
  });
}
</script>"""


def _site_json(path, ttl=600):
    from staff.app import site_json
    return site_json(path, ttl=ttl) or {}


def notes():
    """{precedent parent id: GreMi's note} and {change id: change} from the race-stats repo, cached 10 min."""
    now = time.time()
    with _lock:
        if _notes["data"] is not None and now - _notes["at"] < 600:
            return _notes["data"]
    from staff.app import gh_file
    out = {"grades": {}, "changes": {}}
    for path in ("grading/decisions-seed.json", "grading/decisions.json"):
        try:
            d, _ = gh_file(path)
            for k, v in ((d or {}).get("decisions") or {}).items():
                parts = [p.get("note") for p in v.get("parts") or [] if p.get("note")]
                out["grades"][k] = {"note": v.get("note") or "", "parts": parts}
        except Exception as ex:
            print("learned: notes not read:", path, ex, flush=True)
    try:
        d, _ = gh_file("races/corrections.json")
        for c in (d or {}).get("changes") or []:
            out["changes"][c.get("id")] = c
    except Exception as ex:
        print("learned: changes not read:", ex, flush=True)
    with _lock:
        _notes.update(at=now, data=out)
    return out


def distinct():
    try:
        return set(json.loads(DISTINCT_FILE.read_text()))
    except (FileNotFoundError, ValueError):
        return set()


def distinct_post(form, user):
    """'These two are different': the pair leaves the look-alike list (the calls stay as they are)."""
    key = (form.get("key") or "").strip()
    if "|" not in key or len(key) > 80:
        raise ValueError("Which two incidents?")
    with _lock:
        s = distinct()
        s.add(key)
        DISTINCT_FILE.parent.mkdir(parents=True, exist_ok=True)
        DISTINCT_FILE.write_text(json.dumps(sorted(s)))
    return "Marked as different incidents: they leave this list."


# ---------------------------------------------------------------- the sketch


def sketch_svg(e, sk, cars, width=230):
    """A still top-down picture of the contact: track edges, every car's line (at fault in red), each
    car where it was at the contact, and the contact point. Cars drive up the picture."""
    if not sk or not sk.get("cars"):
        return "<div class=muted style='padding:30px 10px;text-align:center'>No picture: the recording has no data for this one.</div>"
    x0, y0, x1, y1 = sk["box"]
    mid = (x0 + x1) / 2
    x0, x1 = min(x0, mid - 48), max(x1, mid + 48)   # room for the names next to the cars
    w, h = x1 - x0, y1 - y0
    if h > w * 1.6:   # keep it about square: cut the far end of the run-up
        y1 = y0 + w * 1.6
        h = y1 - y0
    fault = {c["car"] for c in cars if c.get("fault")}
    names = {c["car"]: c["name"] for c in cars}
    order = sorted(sk["cars"], key=lambda c: (c["car"] not in fault, cars.index(next((x for x in cars if x["car"] == c["car"]), cars[0])) if cars else 0))
    colors, others = {}, iter(COLORS[1:])
    for c in order:
        colors[c["car"]] = COLORS[0] if c["car"] in fault else next(others, "#d4d4d8")
    cid = f"sk{next(_ids)}"   # one clip per picture: a shared id would clip every picture like the first
    out = [f"<svg viewBox='{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}' width={width} role=img aria-label='Top-down sketch of the incident'>"
           f"<defs><clipPath id='{cid}'><rect x='{x0:.1f}' y='{y0:.1f}' width='{w:.1f}' height='{h:.1f}'/></clipPath></defs><g clip-path='url(#{cid})'>"]
    if sk.get("track"):
        L, R = sk["track"]
        poly = " ".join(f"{x},{y}" for x, y in L + R[::-1])
        out.append(f"<polygon points='{poly}' fill='#1d1b22' stroke='none'/>")
        for edge in (L, R):
            out.append("<polyline points='" + " ".join(f"{x},{y}" for x, y in edge)
                       + "' fill=none stroke='#52525b' stroke-width='0.6'/>")
    for c in order:
        col = colors[c["car"]]
        pts = c.get("path") or []
        if len(pts) > 1:
            out.append("<polyline points='" + " ".join(f"{x},{y}" for x, y in pts)
                       + f"' fill=none stroke='{col}' stroke-width='1.1' stroke-linecap=round stroke-linejoin=round opacity='.85'/>")
    hit = (0, 0)
    out.append(f"<circle cx='{hit[0]}' cy='{hit[1]}' r='4.5' fill='rgba(244,63,94,.22)' stroke='#fb7185' stroke-width='.6'/>")
    for c in order:
        col = colors[c["car"]]
        x, y = c["at"]
        rot = (c.get("heading") or -90) + 90
        out.append(f"<g transform='translate({x},{y}) rotate({rot:.0f})'><rect x='-1' y='-2.8' width='2' height='5.6' rx='.6' "
                   f"fill='{col}' stroke='#0b0a0e' stroke-width='.3'/><rect x='-.8' y='-2.8' width='1.6' height='1' fill='#fff' opacity='.7'/></g>")
    for c in order:
        x, y = c["at"]
        n = names.get(c["car"], f"car {c['car']}")
        side = 1 if x >= 0 else -1
        anchor = "start" if side > 0 else "end"
        out.append(f"<text x='{x + side * 3.2:.1f}' y='{y + 1:.1f}' font-size='3.8' fill='#f4f4f5' text-anchor='{anchor}' "
                   f"paint-order='stroke' stroke='#0b0a0e' stroke-width='1.2' font-family='Geist,system-ui,sans-serif'>{e(n if len(n) <= 14 else n[:13] + '…')}</text>")
    out.append("</g></svg>")
    return "".join(out)


def inputs_html(e, sk, cars):
    if not sk:
        return ""
    fault = {c["car"] for c in cars if c.get("fault")}
    names = {c["car"]: c["name"] for c in cars}
    others = iter(COLORS[1:])
    rows = []
    for c in sorted(sk["cars"], key=lambda c: c["car"] not in fault):
        col = COLORS[0] if c["car"] in fault else next(others, "#d4d4d8")
        rows.append(f"<i style='background:{col}'></i>{e(names.get(c['car'], 'car ' + str(c['car'])))}: {c['kmh']} km/h, "
                    f"throttle {round(c['throttle'] * 100)}%, brake {round(c['brake'] * 100)}%")
    return "<div class=ins><span class=muted>Just before the contact · </span>" + " · ".join(rows) + "</div>"


# ---------------------------------------------------------------- page parts


def _when(entry):
    at = entry.get("at")
    if at:
        return at[:16].replace("T", " ") + " UTC"
    return (entry.get("day") or "") + " (day only: graded in the first calibration batches)"


def _who(entry, staff_name):
    ids = entry.get("who") or []
    if entry.get("by") == "GreMi":
        return "GreMi"
    if ids:
        return ", ".join(dict.fromkeys(staff_name(i) for i in ids))
    return "Staff majority" if entry.get("by") == "staff" else entry.get("by") or "?"


def _fix_link(entry):
    if entry["id"].startswith("s-") and entry.get("file"):
        return f"/races?file={urllib.parse.quote(entry['file'])}#incidents"
    return f"/grading?id={urllib.parse.quote(entry.get('parent') or entry['id'])}"


def _note(entry, n):
    if entry["id"].startswith("s-"):
        rs = [((n["changes"].get(cid) or {}).get("reason") or "") for cid in entry.get("changes") or []]
        return "; ".join(dict.fromkeys(r for r in rs if r))
    g = n["grades"].get(entry.get("parent") or entry["id"]) or {}
    parts = g.get("parts") or []
    if entry.get("part") and len(parts) >= entry["part"]:
        return parts[entry["part"] - 1] or g.get("note", "")
    return g.get("note", "")


def lesson_card(e, entry, n, staff_name):
    src = entry.get("source") or "grade"
    cls = "protest" if src.startswith("protest") else "staff" if src == "staff change" else ""
    cars = entry.get("cars") or []
    fault = [c["name"] for c in cars if c.get("fault")]
    call = entry.get("call") or ""
    head = (f"{e(entry.get('track') or '')} · {e(entry.get('date') or '')}{' · lap ' + e(entry['lap']) if entry.get('lap') else ''}"
            + (f" · part {entry['part']} of {entry['of']}" if entry.get("part") and (entry.get("of") or 0) > 1 else "")
            + (f" · {e(entry['kind'])}" if entry.get("kind") else ""))
    note = _note(entry, n)
    changed = entry.get("changed") or []
    rb = entry.get("rulebook")
    state = entry.get("state") or "active"
    out = [f"<div class='card lesson-card st-{state}' id='lesson-{e(entry['id'])}' data-src='{e(src)}' data-by='{e(entry.get('by'))}' "
           f"data-changed='{1 if changed or entry.get('differs') else 0}'><div class=lesson>"
           f"<div class=sk>{sketch_svg(e, entry.get('sketch'), cars)}</div><div>"
           f"<div class=meta>{e(_when(entry))} · taught by <b>{e(_who(entry, staff_name))}</b><span class='src {cls}'>{e(SOURCE.get(src, src))}</span>"
           + (f" <span class=src style='background:rgba(250,204,21,.12);color:#fde68a'>{'outdated: ' + e(entry.get('outdated')) if state == 'outdated' else 'confirmed the rulebook'}</span>"
              if state != "active" else "") + "</div>"
           f"<h3>{head}</h3>"
           f"<div class=call><b>{e(call)}</b>{' · severity ' + e(entry['severity']) if entry.get('severity') else ''}"
           + (f" <span class=muted>(the rulebook said: {e(rb)})</span>" if rb and rb != call else "")
           + (f"<div class=muted>Involved: " + ", ".join(f"<span style='color:{'#fda4af' if c.get('fault') else '#d4d4d8'}'>{e(c['name'])}</span>" for c in cars) + "</div>" if cars else "")
           + "</div>"]
    if entry.get("why"):
        out.append(f"<div class=why>{e(entry['why'])}</div>")
    if note:
        out.append(f"<div class=quote>{e(note)}</div>")
    out.append(inputs_html(e, entry.get("sketch"), cars))
    if changed:
        out.append(f"<div class=muted style='margin-top:8px'>Changed {len(changed)} other call{'s' if len(changed) > 1 else ''} that look like it:</div><ul class=chg>"
                   + "".join(f"<li>{e(c['track'])} {e(c['date'])}{' L' + e(c['lap']) if c.get('lap') else ''}: "
                             f"<span class=muted>{e(c.get('before') or '')}</span> → <b>{e(c.get('after') or '')}</b>"
                             + (f" · <a href='{e(c['watch'])}' target=_blank rel=noopener>watch</a>" if c.get("watch") else "")
                             + (f" · <a href='/races?file={urllib.parse.quote(c['file'])}#incidents'>change</a>" if c.get("file") else "")
                             + "</li>" for c in changed[:12]) + ("<li class=muted>…</li>" if len(changed) > 12 else "") + "</ul>")
    out.append("<div class=inline style='margin-top:10px;display:flex;gap:6px;flex-wrap:wrap'>"
               + (f"<a class=btn href='{e(entry['watch'])}' target=_blank rel=noopener>▶ Incident viewer</a>" if entry.get("watch") else "")
               + f"<a class=btn href='{e(_fix_link(entry))}'>Change this call</a></div>")
    out.append("</div></div></div>")
    return "".join(out)


def conflict_card(e, c, by_id, n, staff_name, csrf):
    sides = []
    for k in ("a", "b"):
        s = c[k]
        entry = by_id.get(s["id"]) or {"id": s["id"], "cars": []}
        sides.append((s, entry))
    rows = "".join(
        f"<tr><td class=muted>{e(FEATURE_NAMES.get(f, (f, ''))[0])}</td>"
        + "".join(f"<td class='mono{' hl' if f in c['differ'] else ''}'>{_num(s['x'].get(f))}{FEATURE_NAMES.get(f, ('', ''))[1] and ' ' + FEATURE_NAMES[f][1]}</td>" for s, _ in sides)
        + "</tr>" for f in FEATURE_NAMES if any(s["x"].get(f) for s, _ in sides))
    html = [f"<div class=card><div class=muted>Very alike in the numbers (distance {c['d']}), different calls. Either they are less alike than "
            f"the numbers say, or one call is wrong.</div><div class=pair>"]
    for s, entry in sides:
        html.append(f"<div class=side><div class=sk style='max-width:230px'>{sketch_svg(e, entry.get('sketch'), entry.get('cars') or [], 230)}</div>"
                    f"<div style='margin-top:6px'><b>{e(entry.get('track') or '')} {e(entry.get('date') or '')}"
                    f"{' L' + e(entry['lap']) if entry.get('lap') else ''}</b>: {e(s['label'])}</div>"
                    f"<div class=muted>{e(entry.get('call') or '')} · {e(_who(entry, staff_name))} · {e(_when(entry))}</div>"
                    + (f"<div class=why style='font-size:13px'>{e(entry['why'])}</div>" if entry.get("why") else "")
                    + (f"<div class=quote style='font-size:12px'>{e(_note(entry, n))}</div>" if _note(entry, n) else "")
                    + "<div class=inline style='display:flex;gap:6px;flex-wrap:wrap;margin-top:6px'>"
                    + (f"<a class=btn href='{e(entry['watch'])}' target=_blank rel=noopener>▶ watch</a>" if entry.get("watch") else "")
                    + f"<a class=btn href='{e(_fix_link(entry))}'>Change this call</a></div></div>")
    html.append(f"</div><table class=nums style='margin-top:10px'><tr><th></th><th>Left</th><th>Right</th></tr>{rows}</table>"
                f"<form method=post action='/learned-distinct' class=inline style='margin-top:8px'>"
                f"<input type=hidden name=csrf value='{e(csrf)}'><input type=hidden name=key value='{e(c['key'])}'>"
                f"<button>These are different incidents</button> <span class=muted>takes the pair off this list; the calls stay</span></form></div>")
    return "".join(html)


def _links(e, c):
    w = f"<a href='{e(c['watch'])}' target=_blank rel=noopener>watch</a> · " if c.get("watch") else ""
    return w + f"<a href='/races?file={urllib.parse.quote(c.get('file') or '')}#incidents'>change</a>"


def _num(v):
    if v is None:
        return "–"
    return f"{v:g}" if isinstance(v, (int, float)) and not math.isnan(v) else str(v)


def _pct(a, b):
    return f"{round(100 * a / b)}%" if b else "–"


def learned_page(user, q, h):
    from staff.app import staff_name, csrf_token
    e = h["e"]
    d = _site_json("league/learned.json")
    if not d:
        return h["page"]("What I learned", "<h1>What I learned</h1><div class='card muted'>Nothing yet: it comes with the next site build.</div>",
                         user, "learned")
    try:
        n = notes()
    except Exception:
        n = {"grades": {}, "changes": {}}
    log = d.get("log") or []
    by_id = {x["id"]: x for x in log}
    active = [x for x in log if x.get("state") != "outdated"]
    old = [x for x in log if x.get("state") == "outdated"]
    ch = d.get("changes") or {}
    acc = (d.get("accuracy") or [{}])[-1]
    out = ["<h1>What I learned</h1><p class=muted>Every grade, protest and staff call teaches the fault engine. Each lesson below "
           "says when it was taught, by whom, on which incident, what the call was and why, with a picture of the contact. "
           f"Updated {e(d.get('made', ''))}. Only staff can see this page.</p>"]
    out.append("<div class=stats>"
               f"<div class=st><span>Lessons</span><b>{len(log)}</b></div>"
               f"<div class=st><span>Still in use</span><b>{len(active)}</b></div>"
               f"<div class=st><span>Calls changed now</span><b>{ch.get('total', 0)}</b></div>"
               + (f"<div class=st><span>Right without seeing it</span><b>{_pct(acc.get('inc_with_precedents', 0), acc.get('incidents', 0))}</b></div>" if acc else "")
               + f"<div class=st><span>Look alike, different call</span><b>{len(d.get('conflicts') or [])}</b></div></div>")
    out.append("<nav class=lg-tabs><a href='#lessons'>Lessons</a><a href='#changed'>Calls changed by lessons</a>"
               "<a href='#alike'>Look alike, different call</a><a href='#rules'>Rule changes</a><a href='#accuracy'>Accuracy</a></nav>")

    # lessons
    out.append("<h2 id=lessons>Lessons, newest first</h2>"
               "<div class=lg-filter><button type=button class=on onclick=\"lgFilter(this,'all')\">All</button>"
               "<button type=button onclick=\"lgFilter(this,'GreMi')\">GreMi</button>"
               "<button type=button onclick=\"lgFilter(this,'grade')\">Grades</button>"
               "<button type=button onclick=\"lgFilter(this,'protest accepted')\">Protests accepted</button>"
               "<button type=button onclick=\"lgFilter(this,'protest rejected')\">Protests not accepted</button>"
               "<button type=button onclick=\"lgFilter(this,'staff change')\">Staff calls</button>"
               "<button type=button onclick=\"lgFilter(this,'changed')\">Changed something</button></div>")
    if not log:
        out.append("<div class='card muted'>No lessons in the data yet: they come with the next site build.</div>")
    out.append("<div id=lessons>" + "".join(lesson_card(e, x, n, staff_name) for x in active))
    if old:
        out.append(f"<details style='margin-top:12px'><summary class=muted style='cursor:pointer'>{len(old)} outdated lessons "
                   "(a later grade replaced them, or they were only the harmless touches around a graded incident)</summary>"
                   + "".join(lesson_card(e, x, n, staff_name) for x in old) + "</details>")
    out.append("</div>")

    # every call changed by a lesson
    rows = [(c, x) for x in log for c in x.get("changed") or []]
    rows.sort(key=lambda r: (r[0].get("date") or "", r[0].get("track") or ""), reverse=True)
    out.append("<h2 id=changed>Calls changed by lessons</h2><div class=card>"
               f"<p class=muted>{ch.get('total', 0)} calls are different from what the rulebook alone says: "
               f"{(ch.get('counts') or {}).get('cleared', 0)} cleared, {(ch.get('counts') or {}).get('blamed', 0)} blamed, "
               f"{(ch.get('counts') or {}).get('moved', 0)} blame moved. Graded incidents themselves count too; below are the "
               "other incidents that follow a lesson because they look like it.</p>"
               + ("<table><tr><th>Incident</th><th>Rulebook said</th><th>Now</th><th>Because of</th><th></th></tr>"
                  + "".join(f"<tr><td>{e(c['track'])} {e(c['date'])}{' L' + e(c['lap']) if c.get('lap') else ''}</td>"
                            f"<td class=muted>{e(c.get('before') or '')}</td><td><b>{e(c.get('after') or '')}</b></td>"
                            f"<td><a href='#lesson-{e(x['id'])}'>{e(x.get('track') or '')} {e(x.get('date') or '')}</a> "
                            f"<span class=muted>({e(_who(x, staff_name))})</span></td>"
                            f"<td>{_links(e, c)}</td></tr>" for c, x in rows[:80])
                  + "</table>" if rows else "<p class=muted>No other calls follow a lesson right now.</p>") + "</div>")

    # look alike, different call
    gone = distinct()
    cf = [c for c in d.get("conflicts") or [] if c.get("key") not in gone]
    csrf = csrf_token(user)
    out.append("<h2 id=alike>Look alike, different call</h2><p class=muted>Graded incidents whose numbers are almost the same but "
               "that got a different call. New contacts that look like both can't follow either, so one of them should change, "
               "or mark them as different.</p>"
               + ("".join(conflict_card(e, c, by_id, n, staff_name, csrf) for c in cf) or "<div class='card muted'>None right now.</div>"))

    rb = d.get("rulebook") or []
    if rb:
        out.append("<h2 id=rules>Rule changes</h2>" + "".join(
            f"<div class=card><b>{e(r['date'])} · {e(r['version'])}</b> <span class=muted>from {e(r.get('from', ''))}</span><ul>"
            + "".join(f"<li>{e(x)}</li>" for x in r.get("changes") or []) + "</ul></div>" for r in rb))
    accs = d.get("accuracy") or []
    if accs:
        out.append("<h2 id=accuracy>How often I get it right</h2><div class=card><p class=muted>Each graded incident judged as if it "
                   "was never graded (leave one out): with the rulebook only, then with everything else that was graded.</p>"
                   "<table><tr><th>Date</th><th>Rulebook</th><th>Graded incidents</th><th>Rulebook alone</th><th>With grades</th></tr>"
                   + "".join(f"<tr><td>{e(a['date'])}</td><td>{e(a.get('rules', ''))}</td><td>{a['incidents']}</td>"
                             f"<td>{a['inc_rulebook']} ({_pct(a['inc_rulebook'], a['incidents'])})</td>"
                             f"<td><b>{a['inc_with_precedents']} ({_pct(a['inc_with_precedents'], a['incidents'])})</b></td></tr>"
                             for a in reversed(accs)) + "</table></div>")
    return h["page"]("What I learned", STYLE + SCRIPT + "".join(out), user, "learned")
