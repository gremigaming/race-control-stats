"""Incident grading for the staff: calibrates the fault engine with more eyes than GreMi's.

Batches come from the site (league/grading.json: five picks per stream, written by the site build,
each with Claude's take that only GreMi sees) and from staff/grading/ (batch-NN.json, made by hand): incidents with a replay link and the fault engine's call. Staff grade blind
(they never see the engine's call or anyone else's grade), and never grade an incident
they drove in (their /link). GreMi sees everything on the owner-only results page;
his grade is final, otherwise the staff majority counts.

Grades are kept on this server only: GRADES_FILE, {incident id: {discord id: grade}}.
"""
import collections
import contextlib
import fcntl
import json
import os
import pathlib
import re
import time

BATCH_DIR = pathlib.Path(__file__).resolve().parent / "grading"
GRADES_FILE = pathlib.Path(os.environ.get("STAFF_GRADES_FILE", "/var/lib/race-control/grades.json"))
NO, RACING = "Not an incident", "Racing incident"
PICKS = {"low": "low severity", "high": "high severity", "gray": "gray area"}


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


def verdict_of(g):
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


def save(inc, uid, name, form):
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
    gs = grades.get(inc["id"], {})
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


def grading_page(user, q, h):
    """h: helpers from app.py (e, page, csrf_token, is_owner)."""
    e, uid = h["e"], int(user["id"])
    owner = h["is_owner"](uid)
    grades = read_grades()
    me = linked_driver(uid)
    body = ["<h1>Incident grading</h1><p class=muted>Watch each replay, then give your call. "
            "You don't see the fault engine's call or other staff grades, so your view stays your own. "
            "Incidents you drove in are hidden for you. You can change a grade any time.</p>"]
    if owner:
        body.append("<p><a href='/grading/results'>See all staff grades and the engine comparison →</a></p>")
    want = q.get("id", [""])[0]
    for title, incs in batches():
        mine = [i for i in incs if not involved(i, me)]
        done = sum(1 for i in mine if str(uid) in grades.get(i["id"], {}))
        body.append(f"<h2>{e(title)} <span class=muted>· you graded {done} of {len(mine)}</span></h2>")
        for i in mine:
            g = grades.get(i["id"], {}).get(str(uid))
            open_ = " open" if (want == i["id"] or (not want and not g and done < len(mine) and i is next(
                (x for x in mine if str(uid) not in grades.get(x["id"], {})), None))) else ""
            state = f"<span class=pos>✓ {e(verdict_of(g))}" + (f", severity {g['severity']}" if g.get("real") else "") + "</span>" if g else "<span class=muted>not graded</span>"
            checks = "".join(
                f"<label style='display:block'><input type=checkbox name=fault value=\"{e(d['name'])}\""
                f"{' checked' if g and d['name'] in g.get('at_fault', []) else ''}> {e(d['name'])}</label>"
                for d in i["drivers"])
            real = g.get("real") if g else None
            body.append(
                f"<details class=card id='{e(i['id'])}'{open_}><summary style='cursor:pointer'>"
                f"<b>{e(i['id'].upper())}</b> · {e(i['track'])} · {e(i['date'])} · lap {e(i['lap'])} · {e(_drivers(i))}"
                f"<br>{state}</summary>"
                + (f"<details style='margin:0 0 10px'><summary class=muted style='cursor:pointer'>Claude's take "
                   f"({e(PICKS.get(i.get('pick'), 'pick'))}), only you see this; open it after grading</summary>"
                   f"<p class=quote>{e(i['claude'])}</p></details>" if owner and i.get("claude") else "")
                + f"<p><a class=btn href=\"{e(i['replay'])}\" target=_blank rel=noopener>▶ Open replay</a> "
                f"<span class=muted>starts a few seconds before contact ({e(i.get('race_time', ''))} race time)</span></p>"
                f"<form method=post action='/grade'>"
                f"<input type=hidden name=csrf value='{e(h['csrf_token'](user))}'>"
                f"<input type=hidden name=id value='{e(i['id'])}'>"
                f"<p><b>Real incident?</b> <label><input type=radio name=real value=yes required{' checked' if real is True else ''}> Yes</label> "
                f"<label><input type=radio name=real value=no{' checked' if real is False else ''}> No, just racing contact without consequence</label></p>"
                f"<p><b>Who is at fault?</b> <span class=muted>tick nobody for a racing incident</span>{checks}</p>"
                f"<p><b>Severity</b> <span class=muted>1 = tiny, 10 = wrecked a race on purpose</span><br>"
                f"<input type=range name=severity min=1 max=10 value='{(g or {}).get('severity') or 3}' "
                f"oninput='this.nextElementSibling.textContent=this.value' style='width:240px'> <b>{(g or {}).get('severity') or 3}</b></p>"
                f"<p><textarea name=note maxlength=500 placeholder='Why? (optional, helps us tune the rules)' style='min-height:60px'>{e((g or {}).get('note', ''))}</textarea></p>"
                f"<button>{'Update grade' if g else 'Save grade'}</button></form></details>")
    if len(body) == 1 or not batches():
        body.append("<p>No incidents to grade right now.</p>")
    return h["page"]("Grading", "<style>.btn{display:inline-block;background:#3f3f46;color:#fff;padding:6px 12px;"
                     "border-radius:8px}details[open] summary{margin-bottom:8px}</style>" + "".join(body), user, "grading")


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
            staff = "".join(
                f"<div><b>{e(g['name'])}</b>: {e(verdict_of(g))}" + (f" · sev {g['severity']}" if g.get("real") else "")
                + (f"<div class=muted>{e(g['note'])}</div>" if g.get("note") else "") + "</div>"
                for g in sorted(s["staff"].values(), key=lambda g: g["name"].lower())) or "<span class=muted>none yet</span>"
            mine = (f"{e(verdict_of(s['owner']))}" + (f" · sev {s['owner']['severity']}" if s["owner"].get("real") else "")
                    + (f"<div class=muted>{e(s['owner']['note'])}</div>" if s["owner"].get("note") else "")) if s["owner"] else \
                f"<a href='/grading?id={e(i['id'])}#{e(i['id'])}'>grade it</a>"
            eng = i.get("engine") or {}
            mark = "" if s["engine_ok"] is None else ("<span class=pos> ✓</span>" if s["engine_ok"] else "<span class=neg> ✗</span>")
            rows.append(
                f"<tr><td><b>{e(i['id'].upper())}</b><br><a href=\"{e(i['replay'])}\" target=_blank rel=noopener>replay</a>"
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
