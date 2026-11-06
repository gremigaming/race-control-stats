"""Races tab of the staff page (GreMi, 6 Nov 2026): everything about one race in one place.

Pick a race and change all of it: whether it counts at all (remove a test race from the site,
the safety rating, the race viewer, the incident viewer and this page), the results, every
incident (one row per incident with every driver in it: type, severity and a call per driver,
also a driver the telemetry missed), the other points (track limits, DNF, bonuses) and kicks.

Removed races go to race-stats races/test-sessions.json ("races"), which the site build, the
race bundles and the race viewer read (also for the live server's races). Incident calls are
staff changes in races/corrections.json with their call (type, fault, severity) and contact, so
the fault engine learns from them (race-stats scripts/telemetry/precedents.py load_lessons) and
the safety rating applies them (scripts/safety_rating.py apply_staff; "blame" = a driver who
had no points for this contact yet).
"""
import datetime
import json
import re
import secrets
import time
import urllib.parse

from staff import calls

TEST_FILE = "races/test-sessions.json"
_removed_cache = {}


def A():
    from staff import app
    return app


def e(s):
    return A().e(s)


def stamp_of(file):
    m = re.search(r"(\d{4})_(\d\d)_(\d\d)_(\d\d)_(\d\d)_(\d\d)", file or "")
    return datetime.datetime(*map(int, m.groups())) if m else None


def stem(file):
    return re.sub(r"\.json$", "", (file or "").split("/")[-1])


def viewer_url(file, lap=None, names=()):
    """The incident viewer at this incident (the site finds the race from the file, then the contact)."""
    f = file if file.endswith(".json") else file + ".json"
    q = "file=" + urllib.parse.quote(f) + (f"&lap={lap}" if lap else "")
    if names:
        q += "&with=" + urllib.parse.quote("|".join(names))
    return f"{A().SITE}f1/incidents?{q}"


# ---------------------------------------------------------------- removed races


def removed(ttl=30):
    hit = _removed_cache.get("x")
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    data, _ = A().gh_file(TEST_FILE)
    data = data or {"windows": [], "races": []}
    _removed_cache["x"] = (time.time(), data)
    return data


def quali_before(file, files):
    """The qualifying session right before a race on the same track (within 90 minutes), or None."""
    st, track = stamp_of(file), re.sub(r"^Race_|_(Just_in_case_)?\d{4}_.*$", "", stem(file))
    best = None
    for f in files:
        if "Qualifying" not in f or track not in f:
            continue
        s = stamp_of(f)
        if s and st and datetime.timedelta(0) < st - s <= datetime.timedelta(minutes=90) and (not best or s > stamp_of(best)):
            best = f
    return best


def window_for(file, d):
    """When the recording of this session started (Amsterdam time, as the file names): the save is
    written at the end, so back by the longest race time plus the formation lap and a margin."""
    st = stamp_of(file)
    secs = max([float((c.get("final-classification") or {}).get("total-race-time") or 0) for c in d.get("classification-data") or []] or [0])
    if secs <= 0:
        secs = 150 * int((d.get("session-info") or {}).get("total-laps") or 10)
    start = st - datetime.timedelta(seconds=secs + 8 * 60)
    end = st + datetime.timedelta(minutes=2)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def remove_post(form, user):
    app = A()
    file = form.get("file", "")
    files = app.site_json("league/index.json", ttl=60).get("files", [])
    if file not in files:
        raise ValueError("That race is not on the site (already removed?).")
    reason = (form.get("reason") or "").strip()[:200]
    if not reason:
        raise ValueError("Please give a reason.")
    todo = [file]
    if form.get("quali") == "1":
        q = quali_before(file, files)
        if q:
            todo.append(q)
    at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = []
    for f in todo:
        _, d = app.car_names(f)
        lo, hi = window_for(f, d)
        entries.append({"file": stem(f), "from": lo, "to": hi, "kind": "quali" if "Qualifying" in f else "race",
                        "why": reason, "by": app.staff_name(user["id"]), "by_id": str(user["id"]), "at": at})

    def edit(data):
        data.setdefault("windows", [])
        rs = data.setdefault("races", [])
        have = {r.get("file") for r in rs}
        rs.extend(x for x in entries if x["file"] not in have)
    app.save_json(TEST_FILE, edit, f"Staff: removed {', '.join(app.label(f) for f in todo)} ({reason})"[:120],
                  {"windows": [], "races": []})
    _removed_cache.clear()
    return f"Removed {' and '.join(app.label(f) for f in todo)}. It's gone from the site, the viewers and this page in about 5 minutes."


def restore_post(form, user):
    app = A()
    f = stem(form.get("file", ""))

    def edit(data):
        data["races"] = [r for r in data.get("races", []) if r.get("file") != f]
    app.save_json(TEST_FILE, edit, f"Staff: put {app.label(f)} back", {"windows": [], "races": []})
    _removed_cache.clear()
    return f"{app.label(f)} is back. The site has it again in about 5 minutes."


def removed_card(user):
    app = A()
    try:
        data = removed()
    except Exception as ex:
        return f"<div class=card><p class=muted>Removed races could not be loaded: {e(app.explain(ex))}</p></div>"
    rows = ""
    for r in reversed(data.get("races") or []):
        undo = (f'<form method=post action="/race-restore" class=inline onsubmit="return confirm(\'Put this race back?\')">'
                f'{app.field("csrf", app.csrf_token(user))}{app.field("back", "/races")}{app.field("file", r.get("file", ""))}'
                f'<button>Put back</button></form>')
        rows += (f"<tr><td><b>{e(app.label(r.get('file', '')))}</b> <span class=muted>{e(app.day_of(r.get('file', '')))}</span>"
                 f"<div class=muted>{e(r.get('why', ''))} · {e(r.get('by', ''))} · {e((r.get('at') or '')[:16].replace('T', ' '))} UTC</div></td>"
                 f"<td>{undo}</td></tr>")
    for w in reversed(data.get("windows") or []):
        rows += (f"<tr><td><b>{e(w.get('from'))} to {e(w.get('to'))}</b> <span class=muted>(every session in this time)</span>"
                 f"<div class=muted>{e(w.get('why', ''))}</div></td><td class=muted>set in the repo</td></tr>")
    return (f"<div class=card><h2 style='margin-top:0'>Removed races</h2><p class=muted>Not on the site, not in the safety rating, "
            f"the race viewer, the incident viewer or this page. Put one back if it was removed by mistake.</p>"
            f"<table>{rows or '<tr><td class=muted>None.</td></tr>'}</table></div>")


# ---------------------------------------------------------------- incidents of one race


def sr_items(sr, file):
    """{driver name: [items]} of one session in league/safety.json."""
    out = {}
    for d in sr.get("drivers", []):
        for s in d.get("sessions", []):
            if s["file"] == file:
                out[d["name"]] = s["items"]
    return out


def incident_rows(file, sr=None, data=None):
    """Every incident of one session as the editor shows it: where, lap, severity, type, weather,
    the incident viewer link, and each driver with their car, current points (v2.2 scale), their
    SR item and the last staff call. Also used by the protests page."""
    app = A()
    sr = sr if sr is not None else app.site_json("league/safety.json")
    data = data if data is not None else app.corrections()[0]
    incs = [i for i in app.site_json("league/incidents.json", ttl=120).get("incidents", []) if i.get("file") == file]
    names, _ = app.car_names(file)
    car_of = {n: c for c, n in names.items()}
    items = sr_items(sr, file)
    by_t = {}
    for n, its in items.items():
        for it in its:
            if it.get("cat") == "contact" and it.get("inc"):
                by_t[(n, round(it["inc"]["t"], 1))] = it
    fixes = {}
    for c in data.get("changes", []):
        if c.get("type") in ("incident", "confirm") and c.get("file") == file and c.get("inc"):
            fixes[(c.get("car"), round(c["inc"]["t"], 1))] = c
    rows = []
    for i in sorted(incs, key=lambda i: ((i.get("inc") or {}).get("t") or 0)):
        inc = i.get("inc") or {}
        t = round(inc.get("t") or 0, 1)
        cars = dict(zip(inc.get("names") or [], inc.get("pair") or []))
        who = list(dict.fromkeys(list(i.get("lines") or {}) + list(i.get("names") or [])))
        extra = sorted({c.get("car") for (c_, t_), c in fixes.items() if t_ == t} - {cars.get(n, car_of.get(n)) for n in who})
        who += [names.get(x, f"car {x}") for x in extra if x is not None]
        drivers = []
        kind = i.get("type")
        for n in who:
            car = cars.get(n, car_of.get(n))
            if car is None:
                continue
            it = by_t.get((n, t))
            fx = fixes.get((car, t))
            pts = float(it["pts"]) if it else float(((i.get("lines") or {}).get(n) or [0])[0] or 0)
            why = ((i.get("lines") or {}).get(n) or [0, ""])[1]
            call = (fx or {}).get("call") or {}
            if call.get("kind"):
                kind = call["kind"]
            fault = call.get("fault") or ("clear" if pts >= 0 else "shared" if "shared" in (why or "") else "fault")
            drivers.append({"car": car, "name": n, "pts": pts, "why": why, "item": it, "fix": fx, "fault": fault,
                            "ai": bool(app.AI_NAME.search(n))})
        sev = next((int((d["fix"] or {}).get("call", {}).get("severity") or 0) for d in drivers
                    if (d["fix"] or {}).get("call", {}).get("severity")), None) or i.get("sev")
        guessed = not kind
        kind = kind or calls.guess_type(None, " ".join(d["why"] or "" for d in drivers))
        rows.append({"file": file, "t": t, "inc": inc, "lap": i.get("lap"), "where": i.get("where") or "Contact", "sev": sev,
                     "kind": kind, "guessed": guessed and bool(kind), "weather": i.get("weather"),
                     "viewer": viewer_url(file, i.get("lap"), [d["name"] for d in drivers]), "drivers": drivers,
                     "staff": any(d["fix"] for d in drivers)})
    return rows


def incident_post(form, user):
    """One incident's call from the editor: a staff change per driver, saved together."""
    app = A()
    file = form.get("file", "")
    try:
        t = round(float(form.get("t") or ""), 1)
    except ValueError:
        raise ValueError("Which incident? Open the race again.")
    reason = (form.get("reason") or "").strip()[:200]
    if not reason:
        raise ValueError("Please give a reason.")
    sr = app.site_json("league/safety.json")
    data, _ = app.corrections()
    row = next((r for r in incident_rows(file, sr, data) if (r["inc"] or {}).get("t") and abs(r["t"] - t) < 0.05), None)
    if not row:
        raise ValueError("That incident is not in the data any more. Open the race again.")
    kind = calls.read_type(form, "kind")
    if not kind:
        raise ValueError("Pick the incident type (or type a new one).")
    nothing = kind == "Not an incident"
    try:
        sev = max(1, min(10, int(form.get("sev") or 0))) if form.get("sev") else None
    except ValueError:
        raise ValueError("Severity must be 1 to 10.")
    learn = form.get("learn") == "1"
    owner = app.is_owner(int(user["id"]))
    at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    names, _ = app.car_names(file)
    drivers = [d for d in row["drivers"] if not d["ai"]]
    add = form.get("add_car") or ""
    if add.isdigit() and int(add) in names and int(add) not in {d["car"] for d in row["drivers"]}:
        drivers.append({"car": int(add), "name": names[int(add)], "pts": 0.0, "item": None, "fix": None, "new": True})
    others = lambda n: ", ".join(d["name"] for d in drivers if d["name"] != n) or "?"  # noqa: E731
    out = []
    for d in drivers:
        key = "add" if d.get("new") else str(d["car"])
        fault = "clear" if nothing else form.get(f"fault_{key}") or ""
        if fault not in calls.FAULTS:
            continue
        if fault != "clear" and not sev:
            raise ValueError("Give a severity (1-10).")
        call = {"kind": kind, "fault": fault, **({"severity": sev} if sev else {})}
        pts = calls.points(kind, fault, sev)
        typed = (form.get(f"pts_{key}") or "").strip().replace(",", ".")
        if typed and not nothing:
            try:
                pts = app.num(typed, -50, 10)
            except ValueError:
                raise ValueError(f"Points for {d['name']} must be between -50 and 10.")
            call["override"] = True
        it = d["item"]
        c = {"id": secrets.token_hex(6), "type": "incident", "reason": reason, "by": user["id"], "at": at,
             "file": file, "driver": d["name"], "car": d["car"], "pts": pts, "call": call, "learn": learn, "owner": owner}
        if it:
            c.update(id=it["id"], was=float(it.get("orig", it["pts"])), inc=it["inc"], text=(it.get("text") or "")[:200])
        else:
            inc = dict(row["inc"])
            if d.get("new"):
                inc["pair"] = list(inc.get("pair") or []) + [d["car"]]
                inc["names"] = list(inc.get("names") or []) + [d["name"]]
            c.update(id=f"{file}|{d['car']}|contact|{row['lap'] or 0}|s{row['t']:.1f}", was=0.0, inc=inc, blame=True,
                     lap=row["lap"], text=f"{row['where']}, with {others(d['name'])}: staff call ({kind})"[:200],
                     **({"weather": row["weather"]} if row.get("weather") else {}))
        out.append(c)
    if not out:
        raise ValueError("Pick a call for at least one driver.")

    def edit(data):
        ch = data.setdefault("changes", [])
        ids = {c["id"] for c in out}
        spots = {(c["car"], round(c["inc"]["t"], 1)) for c in out}
        ch[:] = [x for x in ch if not (x.get("type") == "incident" and (x.get("id") in ids or (
            x.get("file") == file and x.get("inc") and (x.get("car"), round(x["inc"]["t"], 1)) in spots)))]
        ch.extend(out)
    app.save_corrections(edit, f"Staff: call on {app.label(file)} L{row['lap'] or '?'} {row['where']}: {kind}"[:120])
    return (f"Saved the call for {len(out)} driver{'s' if len(out) != 1 else ''}"
            + (" (the engine learns from it)" if learn else " (leniency, the engine doesn't learn)")
            + ". The site updates in about 2 minutes.")


# ---------------------------------------------------------------- the page


def race_nav(files, file):
    app = A()
    nav, last = "", ""
    for f in files[:160]:
        d = app.day_of(f)
        if d != last:
            nav += f"<div class=day>{e(d)}</div>"
            last = d
        nav += f'<a href="/races?file={urllib.parse.quote(f)}" class="{"on" if f == file else ""}">{e(app.label(f))}</a>'
    return nav


def status_card(user, file, files):
    app = A()
    q = quali_before(file, files) if file.startswith("Race_") else None
    qbox = (f"<label class=muted style='font-size:12px'><input type=checkbox name=quali value=1 checked> also its qualifying "
            f"({e(app.label(q))})</label>") if q else ""
    form = (f'<form method=post action="/race-remove" class=inline onsubmit="return confirm(\'Remove {e(app.label(file))} from everywhere?\')">'
            f'{app.field("csrf", app.csrf_token(user))}{app.field("back", "/races")}{app.field("file", file)}{qbox}'
            f'<input name=reason placeholder="Why (e.g. test race)" required maxlength=200 style="flex:1;min-width:180px">'
            f'<button class=red>Remove race</button></form>')
    return (f"<div class=card id=status><h2 style='margin-top:0'>Status</h2><p><span class=tag style='background:#14532d;color:#bbf7d0;margin:0'>"
            f"Counts</span> <span class=muted>On the site, in the safety rating and in the viewers.</span></p>"
            f"<details><summary class=muted style='cursor:pointer'>Remove this race (test race, not a stream race…)</summary>"
            f"<p class=muted>Removes it from everything: results, safety rating, race viewer, incident viewer and this page. "
            f"The race file is kept, so you can put it back under Races → Removed races.</p>{form}</details></div>")


def kicks_card(user, file):
    app = A()
    names, _ = app.car_names(file)
    day = app.day_of(file)
    data, _ = app.corrections()
    done = [c for c in data.get("changes", []) if c.get("type") == "kick" and any(x.get("file") == file for x in c.get("cars", []))]
    back = f"/races?file={urllib.parse.quote(file)}#kicks"
    opts = "".join(f'<option value="{e(file)}|{car}">{e(n)}</option>' for car, n in sorted(names.items(), key=lambda x: x[1].lower())
                   if not app.AI_NAME.search(n))
    form = (f'<form method=post action="/change" class=inline onsubmit="return confirm(\'Kick this driver from this race?\')">'
            f'{app.field("csrf", app.csrf_token(user))}{app.field("back", back)}{app.field("type", "kick")}{app.field("day", day)}'
            f'<select name=cars required onchange="this.form.driver.value=this.options[this.selectedIndex].text">'
            f'<option value="" disabled selected>driver</option>{opts}</select>{app.field("driver", "")}'
            f'<input name=reason placeholder="Reason (shown on the site)" required maxlength=200 style="flex:1;min-width:180px">'
            f'<button class=red>Kick from this race</button></form>')
    rows = "".join(app.change_row(user, c) for c in done)
    return (f"<div class=card id=kicks><h2 style='margin-top:0'>Kicks</h2><p class=muted>A kicked driver's result and every contact, "
            f"penalty and overtake with them go, for everyone; the others move up. They still lose SR for their own incidents. "
            f"To kick someone from more sessions of the stream at once, use <a href='/kick?day={e(day)}'>Kick for the whole stream</a>.</p>"
            f"{form}" + (f"<table style='margin-top:8px'>{rows}</table>" if rows else "") + "</div>")


def incidents_card(user, file, sr, data, scale):
    app = A()
    back = f"/races?file={urllib.parse.quote(file)}"
    try:
        rows = incident_rows(file, sr, data)
    except Exception as ex:
        return f"<div class=card id=incidents><p class=muted>Incidents could not be loaded: {e(app.explain(ex))}</p></div>"
    names, _ = app.car_names(file)
    addable = {c: n for c, n in names.items() if not app.AI_NAME.search(n)}
    csrf = app.csrf_token(user)
    def one(r):
        if not (r["inc"] or {}).get("t"):   # data from before the contact time was kept: can't be matched safely
            return (f"<div class=incrow><b>{e(r['where'])}</b>{' · lap ' + e(r['lap']) if r.get('lap') else ''} · "
                    + e(" vs ".join(d["name"] for d in r["drivers"]))
                    + "<div class=muted>Editable after the next site build (this data has no contact time yet).</div></div>")
        return calls.incident_editor(e, r, csrf, back + f"#inc-{r['t']:.1f}", scale, addable)
    html_rows = "".join(one(r) for r in rows)
    return (f"<div class=card id=incidents><h2 style='margin-top:0'>Incidents ({len(rows)})</h2>"
            f"<p class=muted>One row per incident with everyone in it. Pick what happened, how bad it was and who was to blame; "
            f"the points follow. {e(calls.scale_text(scale))} Add a driver the telemetry missed with <i>+ driver</i>. "
            f"Every call teaches the fault engine; untick <i>engine learns from this</i> when you only soften the points.</p>"
            f"{html_rows or '<p class=muted>No incidents in this session.</p>'}</div>")


def points_card(user, file, sr, scale):
    """Points that are not a contact: track limits, pit exit, DNF, bonuses, and staff penalties."""
    app = A()
    data, _ = app.corrections()
    pending = {c["id"]: c for c in data.get("changes", []) if c.get("type") == "incident"}
    back = f"/races?file={urllib.parse.quote(file)}#points"
    rows = []
    for name, its in sr_items(sr, file).items():
        for it in its:
            if it.get("id") and it.get("cat") != "contact":
                rows.append((name, it))
    rows.sort(key=lambda r: (r[1]["pts"] >= 0, r[1].get("lap") or 0, r[0].lower()))
    html_rows = ""
    for name, it in rows:
        live = calls.live_pts(it["pts"], scale, it.get("cat"))
        if it["cat"] == "staff":
            ch = next((c for c in data.get("changes", []) if c.get("id") == it["id"]), None)
            change = app.change_row(user, ch) if ch else ""
            html_rows += (f"<tr><td class='mono {app.pts_class(live)}'>{app.signed(round(live, 2))}</td><td><b>{e(name)}</b> · staff"
                          f"<div class=muted>{e(it['text'])}</div>{('<table>' + change + '</table>') if change else ''}</td></tr>")
            continue
        p = pending.get(it["id"])
        note = (f'<span class=tag>staff: was {app.signed(it.get("orig", 0))}</span>' if it.get("staff") is not None
                else f'<span class=tag>saved: {app.signed(p["pts"])}, live soon</span>' if p else "")
        form = app.change_form(user, back, {"type": "incident", "id": it["id"], "file": file, "driver": name,
                                            "car": it["id"].split("|")[1], "was": it.get("orig", it["pts"])},
                               f'<input type=number name=pts step=0.5 min=-50 max=10 value="{e(it["pts"])}" title="New points (v2.2 scale)">')
        html_rows += (f"<tr><td class='mono {app.pts_class(live)}'>{app.signed(round(live, 2))}</td><td><b>{e(name)}</b>"
                      f"{' · lap ' + e(it['lap']) if it.get('lap') else ''} · {e(it['cat'])}{note}<div class=muted>{e(it['text'])}</div>"
                      f"<details><summary class=muted style='cursor:pointer'>Change</summary>{form}</details></td></tr>")
    names, _ = app.car_names(file)
    opts = "".join(f'<option value="{i}">{e(n)}</option>' for i, n in sorted(names.items(), key=lambda x: x[1].lower())
                   if not app.AI_NAME.search(n))
    add = app.change_form(user, back, {"type": "add", "file": file},
                          f'<select name=car required>{opts}</select>'
                          f'<input type=number name=pts step=0.5 min=-50 max=10 value="-2" title="Points (minus for a penalty, v2.2 scale)">'
                          f'<input type=number name=lap min=1 max=99 placeholder="Lap" title="Lap (optional)">'
                          f'<input name=text placeholder="What happened" required maxlength=120>', "Add")
    return (f"<div class=card id=points><h2 style='margin-top:0'>Other points</h2>"
            f"<p class=muted>Track limits, pit exit, DNF and bonuses (live SR points on the left). Points are typed on the v2.2 "
            f"scale, as before; 0 cancels one. Clearing a driver's only penalty gives them their clean-race points back.</p>"
            f"<table>{html_rows or '<tr><td class=muted>None.</td></tr>'}</table>"
            f"<h2>Add a penalty or bonus</h2>{add}</div>")


def races_page(user, q):
    app = A()
    idx = app.site_json("league/index.json", ttl=120)
    files = [f for f in idx.get("files", []) if f.startswith("Race_") or "Qualifying" in f]
    file = q.get("file", [""])[0]
    nav = race_nav(files, file) + '<div class=day>Removed</div><a href="/races?removed=1">Removed races</a>'
    if q.get("removed") or not file:
        body = removed_card(user) if q.get("removed") else "<p class=muted>Pick a race on the left.</p>" + removed_card(user)
        return app.page("Races", f"<h1>Races</h1><p class=muted>Everything about one race in one place: whether it counts, "
                        f"the results, the incidents, other points and kicks.</p>"
                        f"<div class=grid><div class='card list'>{nav}</div><div>{body}</div></div>", user, "races")
    if file not in files:
        body = ("<div class=card><p>This race is not on the site: it was removed, or the name changed.</p></div>" + removed_card(user))
        return app.page("Races", f"<h1>Races</h1><div class=grid><div class='card list'>{nav}</div><div>{body}</div></div>", user, "races")
    sr = app.site_json("league/safety.json")
    data, _ = app.corrections()
    scale = sr.get("scale")
    jump = ("<div class=card style='position:sticky;top:56px;z-index:1;padding:8px 16px'>"
            + " · ".join(f"<a href='#{k}'>{t}</a>" for k, t in (("status", "Status"), ("results", "Results"),
                                                                 ("incidents", "Incidents"), ("points", "Other points"), ("kicks", "Kicks")))
            + "</div>")
    results = app.result_detail(user, file).replace("<div class=card>", "<div class=card id=results>", 1)
    body = (calls.scale_script(scale) + jump + status_card(user, file, files) + results
            + incidents_card(user, file, sr, data, scale) + points_card(user, file, sr, scale) + kicks_card(user, file))
    return app.page(app.label(file), f"<h1>{e(app.label(file))} <span class=muted>{e(app.day_of(file))}</span></h1>"
                    f"<div class=grid><div class='card list'>{nav}</div><div>{body}</div></div>", user, "races")
