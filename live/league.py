"""Receive finalized race artifacts and publish current league JSON to R2.

Uploads arrive over the existing authenticated live token. Raw race saves,
recordings and encrypted captures stay in DATA_DIR; only the existing public
builder outputs are copied to R2. The index is replaced last, so every
generation is internally consistent.
"""
import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from live import final, publish

RACE_DAY = re.compile(r"^\d{4}_\d{2}_\d{2}$")
ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,180}$")
MAX_RACE = 32 * 1024 * 1024
MAX_TELEMETRY = 512 * 1024 * 1024
MAX_CAPTURE = 64 * 1024 * 1024
PUBLISH_DELAY = 2.0
_changed = asyncio.Event()
_loop = None
# A new race or qualifying save arrived (GreMi 2026-11-22): its results must go out within seconds,
# so it stops the slow incident analysis of the build before it (on 20 Nov a rulebook change made
# that analysis run 30+ minutes, and the results overlay and the Streamer.bot scene switch gave up).
_race_in = threading.Event()
# How fast each race save reached the website (GreMi 2026-11-25: within 2 minutes of the save):
# name -> arrival time until a publish includes it, then a row in /status league_recent
_arrived = {}
_recent = []


class Preempted(Exception):
    """A slow analysis step was stopped because a new race save is waiting to be published."""


def notify():
    if _loop is not None:
        _loop.call_soon_threadsafe(_changed.set)
    else:
        _changed.set()


def _target(meta, data_dir):
    if not isinstance(meta, dict) or meta.get("type") != "artifact":
        raise ValueError("expected an artifact header")
    kind, name, day = meta.get("kind"), meta.get("name"), meta.get("day")
    size = meta.get("size")
    if kind not in ("race", "telemetry", "classification"):
        raise ValueError("unsupported artifact kind")
    if not isinstance(name, str) or not ASSET_NAME.fullmatch(name) or os.path.basename(name) != name:
        raise ValueError("invalid artifact name")
    if not isinstance(day, str) or not RACE_DAY.fullmatch(day):
        raise ValueError("invalid race day")
    if not isinstance(size, int) or size <= 0:
        raise ValueError("invalid artifact size")
    suffix, limit = {
        "race": (".json", MAX_RACE),
        "telemetry": (".gmtel.gz", MAX_TELEMETRY),
        "classification": (".gmcls", MAX_CAPTURE),
    }[kind]
    if not name.lower().endswith(suffix) or size > limit:
        raise ValueError("artifact type or size is not allowed")
    if kind == "race":
        folder = os.path.join(data_dir, "league", "incoming", "races", day)
    elif kind == "telemetry":
        folder = os.path.join(data_dir, "telemetry-cache")
    else:
        folder = os.path.join(data_dir, "telemetry-cache", "classification")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name), size


async def receive(ws, data_dir):
    """Read one JSON header then binary frames until exactly the declared size arrives."""
    header = await ws.recv()
    if not isinstance(header, str):
        await ws.send(json.dumps({"ok": False, "error": "artifact header must be text"}, separators=(",", ":")))
        return
    try:
        meta = json.loads(header)
        target, size = _target(meta, data_dir)
    except (ValueError, TypeError) as ex:
        await ws.send(json.dumps({"ok": False, "error": str(ex)}, separators=(",", ":")))
        return

    temp = target + ".upload-" + uuid.uuid4().hex
    received = 0
    digest = hashlib.sha256()
    try:
        with open(temp, "xb") as output:
            while received < size:
                chunk = await asyncio.wait_for(ws.recv(), timeout=90)
                if not isinstance(chunk, bytes) or not chunk or received + len(chunk) > size:
                    raise ValueError("invalid artifact data frame")
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        hexdigest = digest.hexdigest()
        duplicate = False
        if os.path.exists(target):
            old = hashlib.sha256()
            with open(target, "rb") as current:
                for block in iter(lambda: current.read(1024 * 1024), b""):
                    old.update(block)
            duplicate = old.hexdigest() == hexdigest
        if not duplicate:
            os.replace(temp, target)
            if meta["kind"] == "race":
                _arrived.setdefault(meta["name"], time.time())
                _race_in.set()
        else:
            os.remove(temp)
        notify()
        await ws.send(json.dumps({"ok": True, "status": "duplicate" if duplicate else "stored", "sha256": hexdigest}, separators=(",", ":")))
    except Exception:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


LEAGUE_CODE = 7   # bump to make the live server rebuild its league data after a change here


def _head(checkout):
    try:
        return subprocess.run(["git", "-C", checkout, "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return "unknown"


def sr_rules(checkout):
    """The SR rule set the site build uses (race-stats site.yml), so live data never runs older rules."""
    try:   # the checkout is sparse (no .github folder), so read the file from git
        yml = subprocess.run(["git", "-C", checkout, "show", "HEAD:.github/workflows/site.yml"], check=True,
                             capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"SR_RULES:\s*([\w.]+)", yml)
        return m.group(1) if m else "v2.2"
    except Exception:
        return "v2.2"


def needs_reconcile(data_dir):
    """A cheap check after the five-minute source pull; corrections/code changes rebuild live data."""
    checkout = final.checkout(data_dir, pull=False)
    head = _head(checkout)
    state_path = os.path.join(data_dir, "league", "publish-state.json")
    try:
        old = json.load(open(state_path, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    return (old.get("source_revision") != head or old.get("pending") is True
            or old.get("sr_rules") != sr_rules(checkout) or old.get("league_code") != LEAGUE_CODE)


def _kill(proc):
    """Stop a step with all its helpers (the fault engine runs a process pool)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        proc.kill()


def _run(cmd, cwd, env, label, timeout=1800, abortable=False):
    """Run one build step. abortable: a new race save stops it (Preempted), the next build redoes it.

    The next build starts by wiping the league output folder (build-league-data.mjs), so a stopped
    step's half-written output there is never published; steps that keep files elsewhere (telemetry
    cache, OCR cache) write them atomically. Only the first results build is not abortable.
    """
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    deadline = time.time() + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            stop = abortable and _race_in.is_set()
            if stop or time.time() > deadline:
                _kill(proc)
                proc.communicate()
                if stop:
                    raise Preempted(f"{label} stopped for a new race save")
                raise RuntimeError(f"{label} timed out after {timeout} seconds")
    if out.strip():
        print(f"[{label}] {out.strip()[-5000:]}", flush=True)
    if err.strip():
        print(f"[{label}] {err.strip()[-3000:]}", flush=True)
    if proc.returncode:   # the last error line goes into /status, so a crash can be read without the server logs
        lines = [x.strip() for x in (err or "").strip().splitlines() if x.strip()]
        where = [x for x in lines if x.startswith("File ")][-1:]
        tail = " | ".join(where + lines[-1:])
        raise RuntimeError(f"{label} exited with status {proc.returncode}: {tail[-400:]}")


def rebuild(data_dir, status, note):
    """Build provisional results, publish them, then enrich and atomically republish."""
    started = time.time()
    # No git pull here: the server pulls race-stats every few minutes in the background (server.warm),
    # so a new race never waits on GitHub. The new save itself comes in through the overlay folder.
    checkout = final.checkout(data_dir, pull=False)
    source_revision = _head(checkout)
    source = os.path.join(checkout, "races")
    overlay = os.path.join(data_dir, "league", "incoming", "races")
    output = os.path.join(data_dir, "league", "build", "public", "league")
    cache = os.path.join(data_dir, "league", "cache")
    telemetry = os.path.join(data_dir, "telemetry-cache")
    os.makedirs(cache, exist_ok=True)
    os.makedirs(os.path.join(telemetry, "classification"), exist_ok=True)
    os.makedirs(overlay, exist_ok=True)

    env = os.environ.copy()
    env.update({
        "LEAGUE_RACES_DIR": source,
        "LEAGUE_EXTRA_RACES_DIR": overlay,
        "LEAGUE_OUT_DIR": output,
        "LEAGUE_SESSIONS_DIR": os.path.join(output, "sessions"),
        "LEAGUE_CACHE_DIR": cache,
        "CLASSIFICATION_FILE": os.path.join(cache, "classification.json"),
        "SR_OUT": output,
        "SR_VERDICTS": os.path.join(telemetry, "verdicts"),
        "AWARDS_OUT": os.path.join(output, "awards.json"),
        "SR_RULES": sr_rules(checkout),
        # Incident replays scan whole recordings; the first run on an empty cache can take
        # hours. Stop starting new race days after 15 min; the next build picks up the rest.
        "REPLAY_BUDGET_S": "900",
    })
    py = sys.executable

    # Get result JSON onto R2 first; network OCR and historical telemetry work
    # can continue while the visitor sees the new race as provisional. Every save that is in
    # now goes out with this publish; one arriving later stops the analysis below.
    _race_in.clear()
    waiting = dict(_arrived)   # every save that is in now goes out with this first publish
    _run(["node", os.path.join(checkout, "scripts", "build-league-data.mjs")], checkout, env, "league results")
    first = publish.publish_league(output, pending=True)
    status.update({"league_pending": True, "league_generation": first["generation"],
                   "league_last_publish": time.time(), "league_stage": "analysis",
                   "league_first_publish_s": round(time.time() - started, 1)})
    _visible(waiting, status)

    def go_on():   # a save that came in during a step without a subprocess: publish it first
        if _race_in.is_set():
            raise Preempted("build stopped for a new race save")

    # Captures may arrive after the race JSON. Apply OCR and publish hidden-name
    # joins as a second provisional generation before fault analysis completes.
    _run([py, os.path.join(checkout, "scripts", "telemetry", "classification_ocr.py"),
          telemetry, env["CLASSIFICATION_FILE"]], checkout, env, "classification OCR", timeout=300, abortable=True)
    _run(["node", os.path.join(checkout, "scripts", "build-league-data.mjs")], checkout, env, "league names",
         abortable=True)
    go_on()
    first = publish.publish_league(output, pending=True)
    status.update({"league_generation": first["generation"], "league_last_publish": time.time()})

    _run([py, os.path.join(checkout, "scripts", "telemetry", "fault_engine.py"), telemetry], checkout, env,
         "fault engine", abortable=True)
    # Graded incidents as precedents (race-stats grading/), the same step as the site build,
    # so the website (live data) and the Pages build / staff page give the same points.
    try:
        _run([py, os.path.join(checkout, "scripts", "telemetry", "precedents.py"), "apply", telemetry],
             checkout, env, "precedents", timeout=900, abortable=True)
    except Preempted:
        raise
    except Exception as ex:
        print("precedents not applied, verdicts are the rulebook's:", ex, flush=True)
    # Frozen calls (GreMi, 16 Nov 2026): past races keep the calls in race-stats grading/frozen-calls.json
    # (the site build freezes them; read only here, so the live data and Pages never disagree)
    try:
        _run([py, os.path.join(checkout, "scripts", "telemetry", "freeze_calls.py"), telemetry, "--read"],
             checkout, env, "frozen calls", timeout=300, abortable=True)
    except Preempted:
        raise
    except Exception as ex:
        print("frozen calls not applied:", ex, flush=True)
    try:
        _run([py, os.path.join(checkout, "scripts", "safety_rating.py")], checkout, env, "safety rating",
             abortable=True)
    except RuntimeError as ex:   # a new SR rule set that crashes here must not stop live publishing
        if env["SR_RULES"] == "v2.2":
            raise
        note(f"SR {env['SR_RULES']} failed, published with v2.2 instead: {ex}")
        _run([py, os.path.join(checkout, "scripts", "safety_rating.py")], checkout, dict(env, SR_RULES="v2.2"),
             "safety rating v2.2", abortable=True)
    # Performance Rating (GreMi, 25 Nov 2026), the same step as the site build. A failure here only
    # leaves the previous performance.json out of this generation; it must not stop live publishing.
    if os.path.exists(os.path.join(checkout, "scripts", "performance_rating.py")):
        try:
            _run([py, os.path.join(checkout, "scripts", "performance_rating.py"), telemetry], checkout, env,
                 "performance rating", timeout=1800, abortable=True)
        except Preempted:
            raise
        except Exception as ex:
            print("performance rating not built:", ex, flush=True)
    _run([py, os.path.join(checkout, "scripts", "awards.py")], checkout, env, "awards", abortable=True)
    _run([py, os.path.join(checkout, "scripts", "telemetry", "incident_replays.py"), telemetry], checkout, env, "incident replays",
         abortable=True)
    go_on()
    result = publish.publish_league(output, pending=False)

    state_path = os.path.join(data_dir, "league", "publish-state.json")
    with open(state_path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"source_revision": source_revision, "sr_rules": env["SR_RULES"], "league_code": LEAGUE_CODE,
                   "generation": result["generation"],
                   "pending": False, "published_at": time.time()}, fh)
    os.replace(state_path + ".tmp", state_path)
    status.update({"league_pending": False, "league_generation": result["generation"],
                   "league_last_publish": time.time(), "league_stage": "ready",
                   "league_duration_s": round(time.time() - started, 1)})
    print(f"live league generation {result['generation']} published in {time.time() - started:.1f}s", flush=True)
    return result


def _visible(waiting, status):
    """Book how long each new race save took from arriving here to being on the website."""
    now = time.time()
    for name, at in waiting.items():
        if _arrived.get(name) == at:
            del _arrived[name]
        _recent.append({"race": name, "received": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(at)),
                        "visible_in_s": round(now - at, 1)})
        print(f"live league: {name} on the website {now - at:.1f}s after it arrived", flush=True)
    del _recent[:-20]
    status["league_recent"] = list(_recent)


async def publish_worker(data_dir, status, note):
    """Coalesce near-simultaneous uploads and retry failed or newly pulled sources."""
    global _loop
    _loop = asyncio.get_running_loop()
    # Reconcile on boot once code and R2 credentials are available.
    notify()
    while True:
        await _changed.wait()
        _changed.clear()
        await asyncio.sleep(PUBLISH_DELAY)
        while _changed.is_set():
            _changed.clear()
            await asyncio.sleep(0.5)
        status["league_stage"] = "building"
        try:
            await asyncio.to_thread(rebuild, data_dir, status, note)
        except Preempted as ex:   # publish the new save now, then start the analysis again
            status["league_stage"] = "restarting for a new race"
            print(f"live league: {ex}", flush=True)
            notify()
        except Exception as ex:
            status["league_pending"] = True
            status["league_stage"] = "error"
            note(f"live league publish failed: {ex}")
            # Keep provisional output visible and retry shortly without blocking the server.
            await asyncio.sleep(30)
            notify()
