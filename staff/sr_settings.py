"""SR settings tab of the staff page: every number of the safety rating (SR v2.5).

Saves races/sr-settings.json in gremigaming/race-stats. That push rebuilds the website
(about 3 minutes) and the live server picks it up on its next source pull (every 5 minutes)
and republishes the league data. scripts/sr_settings.py in race-stats reads the file and
checks every value again; SR_FIELDS here must match its FIELDS (same keys and limits).

Everyone on the staff list can look; only the owner can save.
"""
import datetime

PATH = "races/sr-settings.json"

# (key, default, min, max, kind, label, hint)  kind: num, int, bool, list
GROUPS = [
    ("Penalty points", "What a penalty costs before the multipliers below. Shared blame costs half.", [
        ("contact_small", 0.75, 0, 100, "num", "Contact, severity 1-3", ""),
        ("contact_medium", 2.25, 0, 100, "num", "Contact, severity 4-5", ""),
        ("contact_big", 4.5, 0, 100, "num", "Contact, severity 6-7", ""),
        ("contact_huge", 7.5, 0, 100, "num", "Contact, severity 8-10", ""),
        ("contact_r9", 15.0, 0, 200, "num", "Hitting the same car again on purpose (R9)", ""),
        ("contact_other", 0.5, 0, 20, "num", "Staff contact penalty with another value",
         "x the points you typed (v2.2 scale)"),
        ("penalty_track", 1.5, 0, 20, "num", "Track limits / pit exit / disqualified",
         "x the game's points: 1 per track limit, 10 for a DSQ"),
        ("penalty_dnf", 1.5, 0, 20, "num", "Quitting a race", "x 1 point"),
    ]),
    ("Bonus points", "Each bonus = its base value x the bonus multiplier x its own multiplier.", [
        ("bonus_scale", 2.0, 0, 20, "num", "Bonus multiplier (all bonuses)", ""),
        ("bonus_finish", 1.0, 0, 10, "num", "Finishing", "base 0.5, or 1 without qualifying"),
        ("bonus_clean", 1.0, 0, 10, "num", "Clean laps and a perfect race", "base 0.3 per clean lap, +1 perfect race"),
        ("bonus_close", 1.0, 0, 10, "num", "Close racing", "base 0.2 per close clean sector"),
        ("bonus_overtakes", 1.0, 0, 10, "num", "Clean overtakes", "base 0.5 per 3 in a row, at most 1.5"),
        ("bonus_quali", 1.0, 0, 10, "num", "Clean qualifying", "base 0.5"),
        ("bonus_stream", 1.0, 0, 10, "num", "First race of the stream / full telemetry", "base 1 each"),
        ("bonus_cut", 0.10, 0, 1, "num", "Bonus cut per 10 SR above 50", "0.10 = 10% less bonus per 10 SR"),
        ("no_bonus_dirty", True, None, None, "bool", "No bonus in a dirty race", ""),
    ]),
    ("Penalty multiplier by SR", "Your SR before the race. Below 60 penalties count x1.", [
        ("tier_60", 1.2, 0, 10, "num", "SR 60-69", ""),
        ("tier_70", 1.4, 0, 10, "num", "SR 70-79", ""),
        ("tier_80", 1.6, 0, 10, "num", "SR 80-89", ""),
        ("tier_90", 1.8, 0, 10, "num", "SR 90-99", ""),
        ("tier_100", 2.0, 0, 10, "num", "SR 100", ""),
    ]),
    ("Dirty races", "A race turns dirty at the first of these counts; penalties after that use the streak multiplier.", [
        ("dirty_big", 1, 1, 20, "int", "Big incidents (severity 6+ / R9)", ""),
        ("dirty_medium", 2, 1, 20, "int", "Medium incidents (severity 4-5)", ""),
        ("dirty_small", 3, 1, 20, "int", "Small incidents (severity 1-3)", ""),
        ("streak_first", 1.5, 1, 10, "num", "Streak multiplier, first step", ""),
        ("streak_step", 0.5, 0, 10, "num", "Added per extra dirty race in a row", ""),
        ("streak_max", 0.0, 0, 20, "num", "Highest streak multiplier", "0 = no limit"),
    ]),
    ("Gravity", "Share of (SR - 50) taken after every league race, booked on your latest race.", [
        ("gravity_joined", 0.04, 0, 0.5, "num", "After a race you joined", "0.04 = 4%"),
        ("gravity_skipped", 0.02, 0, 0.5, "num", "After a race you skipped", "0.02 = 2%"),
    ]),
    ("Race window", "SR = 50 + the weighted net scores of your own last races.", [
        ("window_step", 6, 1, 50, "int", "Races per step", ""),
        ("window_weights", [1.0, 0.8, 0.6, 0.4, 0.2], 0, 1, "list", "Weight of each step, newest first",
         "comma-separated; window = races per step x number of weights"),
    ]),
    ("Staff alerts", "A driver gets flagged for the mods when they lose this much.", [
        ("kick_race", 30.0, 1, 1000, "num", "Kick line, one race", ""),
        ("kick_stream", 60.0, 1, 1000, "num", "Kick line, one stream", ""),
    ]),
]
FIELDS = [f for _, _, fs in GROUPS for f in fs]
DEFAULTS = {f[0]: f[1] for f in FIELDS}
HISTORY_MAX = 30


def check(key, value):
    spec = next((f for f in FIELDS if f[0] == key), None)
    if spec is None:
        raise ValueError(f"unknown setting {key}")
    _, _, lo, hi, kind, label, _ = spec
    if kind == "bool":
        return str(value).lower() in ("true", "on", "1", "yes")
    if kind == "list":
        parts = value if isinstance(value, list) else str(value).replace(";", ",").split(",")
        out = [_num(label, p, lo, hi) for p in parts if str(p).strip() != ""]
        if not 1 <= len(out) <= 20:
            raise ValueError(f"{label}: give 1 to 20 numbers")
        return out
    v = _num(label, value, lo, hi)
    if kind == "int":
        if v != int(v):
            raise ValueError(f"{label}: must be a whole number")
        return int(v)
    return v


def _num(label, value, lo, hi):
    if isinstance(value, bool):
        raise ValueError(f"{label}: not a number")
    try:
        v = float(str(value).strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"{label}: '{value}' is not a number") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{label}: not a number")
    if not lo <= v <= hi:
        raise ValueError(f"{label}: must be between {lo:g} and {hi:g}")
    return v


def current(data):
    vals = dict(DEFAULTS)
    for k, v in ((data or {}).get("values") or {}).items():
        try:
            vals[k] = check(k, v)
        except ValueError:
            pass
    return vals


def show(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(f"{x:g}" for x in v)
    return f"{v:g}"


def from_form(form, old):
    """New values from the posted form; ValueError names every bad field."""
    new, bad = {}, []
    for key, _, _, _, kind, label, _ in FIELDS:
        raw = form.get(key, "") if kind != "bool" else form.get(key, "off")
        if kind != "bool" and str(raw).strip() == "":
            new[key] = old[key]
            continue
        try:
            new[key] = check(key, raw)
        except ValueError as ex:
            bad.append(str(ex))
    if bad:
        raise ValueError("; ".join(bad))
    return new


def apply(data, new, by, reason):
    """Writes new values into the file data; returns {key: [old, new]} of what changed."""
    old = current(data)
    changed = {k: [old[k], v] for k, v in new.items() if old[k] != v}
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["values"] = {k: new.get(k, old[k]) for k in DEFAULTS}
    data.update(updated=now, by=by, reason=reason)
    if changed:
        data["history"] = ([{"at": now, "by": by, "reason": reason, "changed": changed}]
                           + list(data.get("history") or []))[:HISTORY_MAX]
    return changed


def page_body(e, data, live, owner, csrf):
    """The SR settings tab. live: the settings the published safety.json was built with (or None)."""
    vals = current(data)
    waiting = [k for k in DEFAULTS if live is not None and live.get(k, DEFAULTS[k]) != vals[k]]
    if live is None:
        status = "<div class=card>Couldn't read the published SR data, so it's unknown whether the site already uses these values.</div>"
    elif waiting:
        status = (f"<div class=alert>Saved, not live yet: {len(waiting)} value{'s' if len(waiting) > 1 else ''} "
                  "still building. The website takes about 3 minutes, the live data up to about 10.</div>")
    else:
        status = "<div class=flash>The site runs on these values.</div>"
    dis = "" if owner else " disabled"
    rows = []
    for title, about, fs in GROUPS:
        rows.append(f"<h2>{e(title)}</h2><div class=muted>{e(about)}</div><div class=card><table>")
        for key, default, lo, hi, kind, label, hint in fs:
            v = vals[key]
            if kind == "bool":
                inp = f"<input type=checkbox name={key}{' checked' if v else ''}{dis}>"
            elif kind == "list":
                inp = f"<input name={key} value='{e(show(v))}' style='width:220px'{dis}>"
            else:
                step = "1" if kind == "int" else "any"
                inp = f"<input type=number name={key} value='{e(show(v))}' min={lo:g} max={hi:g} step={step}{dis}>"
            note = []
            if hint:
                note.append(e(hint))
            if v != default:
                note.append(f"default {e(show(default))}")
            if key in waiting:
                note.append(f"<span class=neg>site still on {e(show(live.get(key, default)))}</span>")
            rows.append(f"<tr><td style='width:42%'>{e(label)}</td><td style='width:240px'>{inp}</td>"
                        f"<td class=muted>{' · '.join(note)}</td></tr>")
        rows.append("</table></div>")
    window = vals["window_step"] * len(vals["window_weights"])
    summary = (f"<p class=muted>Window now: last {window} own races. Last saved "
               f"{e((data or {}).get('updated', 'never'))} by {e((data or {}).get('by', '-'))}"
               + (f": {e(data.get('reason'))}" if (data or {}).get("reason") else "") + "</p>")
    form = ""
    if owner:
        form = (f"<form method=post action='/sr-settings'><input type=hidden name=csrf value='{e(csrf)}'>"
                + "".join(rows)
                + "<div class=card style='display:flex;gap:8px;flex-wrap:wrap;align-items:center'>"
                  "<input name=reason placeholder='Why (kept in the history)' required maxlength=200 style='flex:1;min-width:220px'>"
                  "<button>Save and push live</button></div></form>"
                  f"<form method=post action='/sr-settings' onsubmit=\"return confirm('Put every SR setting back to the SR v2.5 defaults?')\">"
                  f"<input type=hidden name=csrf value='{e(csrf)}'><input type=hidden name=do value=reset>"
                  "<input type=hidden name=reason value='Back to the SR v2.5 defaults'>"
                  "<button class=red>Reset everything to the defaults</button></form>")
    else:
        form = "<p class=muted>Only GreMi can change these.</p>" + "".join(rows)
    hist = (data or {}).get("history") or []
    hrows = "".join(
        f"<tr><td class=mono>{e(h.get('at', '')[:16].replace('T', ' '))}</td><td>{e(h.get('by', ''))}</td>"
        f"<td>{e(h.get('reason', ''))}<div class=muted>"
        + ", ".join(f"{e(k)}: {e(show(a))} → {e(show(b))}" for k, (a, b) in (h.get("changed") or {}).items())
        + "</div></td>"
        + (f"<td><form method=post action='/sr-settings'><input type=hidden name=csrf value='{e(csrf)}'>"
           f"<input type=hidden name=do value=undo><input type=hidden name=at value='{e(h.get('at', ''))}'>"
           "<input type=hidden name=reason value='Undo'><button>Undo</button></form></td>" if owner and i == 0 else "<td></td>")
        + "</tr>" for i, h in enumerate(hist))
    history = f"<h2>History</h2><div class=card><table>{hrows or '<tr><td class=muted>No changes yet.</td></tr>'}</table></div>"
    return (f"<h1>SR settings</h1><p class=muted>Every number of the safety rating (SR v2.5). Saving pushes it to the "
            "website and the live data; the rules text on the site follows automatically. A bad value can't be saved, "
            "and the build falls back to the default for any value it can't use.</p>"
            f"{status}{summary}{form}{history}")


def post(form, data, by):
    """Applies a posted form to the file data (in place); returns {key: [old, new]}."""
    reason = (form.get("reason") or "").strip()[:200]
    if not reason:
        raise ValueError("give a reason")
    act = form.get("do", "")
    old = current(data)
    if act == "reset":
        new = dict(DEFAULTS)
    elif act == "undo":
        hist = data.get("history") or []
        if not hist or hist[0].get("at") != form.get("at"):
            raise ValueError("only the latest change can be undone")
        new = dict(old)
        for k, (a, _b) in hist[0]["changed"].items():
            if k in DEFAULTS:
                new[k] = check(k, a)
        reason = f"Undo: {hist[0].get('reason', '')}"[:200]
    else:
        new = from_form(form, old)
    return apply(data, new, by, reason)
