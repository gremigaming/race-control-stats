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
import subprocess
import sys
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


LEAGUE_CODE = 4   # bump to make the live server rebuild its league data after a change here


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


def _run(cmd, cwd, env, label, timeout=1800):
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                            timeout=timeout, check=False)
    if result.stdout.strip():
        print(f"[{label}] {result.stdout.strip()[-5000:]}", flush=True)
    if result.stderr.strip():
        print(f"[{label}] {result.stderr.strip()[-3000:]}", flush=True)
    if result.returncode:
        raise RuntimeError(f"{label} exited with status {result.returncode}")


def rebuild(data_dir, status, note):
    """Build provisional results, publish them, then enrich and atomically republish."""
    started = time.time()
    checkout = final.checkout(data_dir)   # league builds need the current scripts + races
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
    # can continue while the visitor sees the new race as provisional.
    _run(["node", os.path.join(checkout, "scripts", "build-league-data.mjs")], checkout, env, "league results")
    first = publish.publish_league(output, pending=True)
    status.update({"league_pending": True, "league_generation": first["generation"],
                   "league_last_publish": time.time(), "league_stage": "analysis"})

    # Captures may arrive after the race JSON. Apply OCR and publish hidden-name
    # joins as a second provisional generation before fault analysis completes.
    _run([py, os.path.join(checkout, "scripts", "telemetry", "classification_ocr.py"),
          telemetry, env["CLASSIFICATION_FILE"]], checkout, env, "classification OCR", timeout=300)
    _run(["node", os.path.join(checkout, "scripts", "build-league-data.mjs")], checkout, env, "league names")
    first = publish.publish_league(output, pending=True)
    status.update({"league_generation": first["generation"], "league_last_publish": time.time()})

    _run([py, os.path.join(checkout, "scripts", "telemetry", "fault_engine.py"), telemetry], checkout, env, "fault engine")
    # Graded incidents as precedents (race-stats grading/), the same step as the site build,
    # so the website (live data) and the Pages build / staff page give the same points.
    try:
        _run([py, os.path.join(checkout, "scripts", "telemetry", "precedents.py"), "apply", telemetry],
             checkout, env, "precedents", timeout=900)
    except Exception as ex:
        print("precedents not applied, verdicts are the rulebook's:", ex, flush=True)
    _run([py, os.path.join(checkout, "scripts", "safety_rating.py")], checkout, env, "safety rating")
    _run([py, os.path.join(checkout, "scripts", "awards.py")], checkout, env, "awards")
    _run([py, os.path.join(checkout, "scripts", "telemetry", "incident_replays.py"), telemetry], checkout, env, "incident replays")
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
        except Exception as ex:
            status["league_pending"] = True
            status["league_stage"] = "error"
            note(f"live league publish failed: {ex}")
            # Keep provisional output visible and retry shortly without blocking the server.
            await asyncio.sleep(30)
            notify()
