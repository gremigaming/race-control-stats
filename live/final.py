"""A finished race, ready to publish seconds after the flag: the same replay the race-stats site
builds, from the session's own recording (live/race.py Session.record), with the same code.

The code is race-stats scripts/telemetry (race_bundles.py: chunks, timing, ghosts, incidents;
verdicts.py: the fault engine), kept as a sparse checkout in DATA_DIR/race-stats and pulled
every few minutes, so a rule change there reaches live races without touching this repo.
If any of it fails, the race is still published from the live chunks (no incidents grouped).
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

REPO = "https://github.com/gremigaming/race-stats.git"
PULL_EVERY = 300  # s
VERDICT_KEYS = ("verdict", "at_fault", "rule", "severity", "why")

_state = {"pulled": 0.0, "head": None, "mods": None}
_lock = threading.RLock()  # the background pull never swaps the modules under a running build


def checkout(data_dir, pull=True):
    """Clone or update the sparse race-stats checkout (only scripts/telemetry). Returns its path."""
    if os.environ.get("RACE_STATS_DIR"):   # tests: a local race-stats checkout
        return os.environ["RACE_STATS_DIR"]
    d = os.path.join(data_dir, "race-stats")
    git = ["git", "-C", d]
    if not os.path.isdir(os.path.join(d, ".git")):
        shutil.rmtree(d, ignore_errors=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", REPO, d],
                       check=True, timeout=120)
        subprocess.run(git + ["sparse-checkout", "set", "scripts/telemetry"], check=True, timeout=60)
    elif pull and time.time() - _state["pulled"] > PULL_EVERY:
        subprocess.run(git + ["pull", "-q", "--ff-only", "--depth", "1"], check=True, timeout=120)
    _state["pulled"] = time.time()
    return d


def modules(data_dir, pull=True):
    """(gmtel, race_bundles, verdicts) from the checkout, reloaded when it moved on.
    The server pulls every few minutes in the background, so a finished race never waits on git."""
    with _lock:
        return _modules(data_dir, pull)


def _modules(data_dir, pull):
    d = checkout(data_dir, pull)
    head = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if _state["mods"] is None or head != _state["head"]:
        path = os.path.join(d, "scripts", "telemetry")
        if path not in sys.path:
            sys.path.insert(0, path)
        mods = []
        for name in ("gmtel", "race_bundles", "incident_replays", "verdicts"):
            mods.append(importlib.reload(sys.modules[name]) if name in sys.modules else importlib.import_module(name))
        _state["mods"] = (mods[0], mods[1], mods[3])
        _state["head"] = head
    return _state["mods"]


def build(session, data_dir):
    """Bundle a finished race into session.dir/final/<uid>/ (manifest.json + chunks/).
    Returns (that folder, manifest) or None when the recording can't be bundled."""
    with _lock:
        return _build(session, data_dir)


def _build(session, data_dir):
    gmtel, rb, verdicts = modules(data_dir, pull=False)
    session.close_recording()
    out = os.path.join(session.dir, "final")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    uid = session.meta["uid"]
    m = rb.bundle(session.rec_path, uid, out)
    if not m:
        return None
    m["started"] = session.meta["started"]
    m["source"] = "live"
    try:
        judge(gmtel, rb, verdicts, session.rec_path, m)
    except Exception as ex:  # the fault engine is still being calibrated: never block the replay on it
        m["verdict_error"] = str(ex)[:200]
    with open(os.path.join(out, uid, "manifest.json"), "w") as f:
        json.dump(m, f, separators=(",", ":"))
    return os.path.join(out, uid), m


def judge(gmtel, rb, verdicts, path, m):
    """The fault engine's verdict on every incident (verdicts.Judge, as verdicts.judge_race does),
    with shown names only (hidden-name players stay "Car #N")."""
    sess = gmtel.Session(path)
    frames = rb.frames_of(sess)
    j = verdicts.Judge(sess, frames, path)
    name = lambda i: m["cars"].get(str(i), {}).get("name", f"car {i}")
    m["rulebook"] = getattr(verdicts, "RULEBOOK", "v0")
    for inc in m["incidents"]:
        v = j.judge(inc, name)
        inc["verdict"] = {k: v[k] for k in VERDICT_KEYS if k in v}
        for d in inc["drivers"]:
            if v.get("at_fault") is not None:
                d["verdict"] = "At fault" if d["car"] == v["at_fault"] else "Not at fault"
            else:
                d["verdict"] = v.get("verdict", "")
