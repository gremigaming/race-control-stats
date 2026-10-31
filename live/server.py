"""Live Race Viewer server (Hetzner, https://live.gremigaming.com behind Caddy).

    wss://live.gremigaming.com/ingest   the racing PC's uplink (Authorization: Bearer INGEST_TOKEN)
    https://live.gremigaming.com/status what is coming in, for checking without SSH (no names, no keys)
    wss://live.gremigaming.com/watch    live viewers (race-stats src/viewer/live.ts), see watch()
    https://live.gremigaming.com/live.json                  sessions streaming now
    https://live.gremigaming.com/s/<uid>/manifest.json      a session's manifest (shown names only)
    https://live.gremigaming.com/s/<uid>/chunks/<name>      its finished 10 s chunks (gzip bytes)
    https://live.gremigaming.com/s/<uid>/tail?after=<t>     newer frames: the 1 s polling fallback

An uplink message is one binary WebSocket frame:
    uint64 stream id (new each time the uploader starts)  uint32 sequence number
    uint64 PC clock (ms)  then raw deflate of records (see live/race.py)
The server answers "ack <sequence>" so the PC can drop what arrived and resend the rest
after a dropout. Sessions are written to DATA_DIR; a race that ends (final classification,
or 2 minutes without data) is bundled with incidents and verdicts (live/final.py) and goes to
R2 (live/publish.py) straight away: on replays.gremigaming.com within seconds of the flag.

Run: python -m live.server (systemd unit race-live, set up by live/bootstrap.sh).
"""

import asyncio
import gzip
import hmac
import json
import logging
import os
import shutil
import struct
import time
import zlib

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from live import final, maps, publish
from live.race import Session, records

DATA_DIR = os.environ.get("DATA_DIR", "/var/lib/race-live")
PORT = int(os.environ.get("PORT", "8100"))
TOKEN = os.environ.get("INGEST_TOKEN", "")
IDLE_FINISH = 120        # s without data: the session is over
LIVE_WITHIN = 30         # s since the last data: the session counts as streaming
PUSH_EVERY = 0.2         # s between pushes of new frames to live viewers
MANIFEST_EVERY = 1.0     # s, at most, between manifest pushes
FORGET_AFTER = 600       # s after finishing: drop it from memory
KEEP_OTHER_DAYS = 3      # practice and qualifying stay on disk this long (races stay)
MESSAGE = struct.Struct("<QIQ")

log = logging.getLogger("race-live")
sessions = {}
state = {"started": time.time(), "uplink": False, "last_batch": 0.0, "batches": 0, "errors": [], "maps": [],
         "rejected": 0, "last_rejected": 0.0}
watchers = {}  # websocket -> uid it watches (None: only the list of live sessions)


def note(text):
    log.warning(text)
    state["errors"] = (state["errors"] + [f"{time.strftime('%H:%M:%S')} {text}"])[-10:]


def session(uid):
    s = sessions.get(uid)
    if s is None:
        s = sessions[uid] = Session(os.path.join(DATA_DIR, "sessions"), uid)
    return s


def take(data):
    """Feed one uplink batch into the sessions (and their recordings, for live/final.py)."""
    for kind, head, body, raw in records(data, raw=True):
        uid = head[6]
        if uid:
            s = session(uid)
            s.record(raw)
            s.add(kind, head, body)


def live_sessions():
    """Sessions streaming now, newest first: what the site's Live tab shows."""
    now = time.time()
    out = []
    for s in sessions.values():
        if s.meta["finished"] or now - s.last_seen > LIVE_WITHIN or s.newest() is None:
            continue
        m = s.manifest()
        out.append({"uid": m["uid"], "started": m["started"], "track": m["track"], "session": m["session"],
                    "laps": m["laps"], "cars": len(m["cars"]), "t": round(s.newest(), 2)})
    return sorted(out, key=lambda r: r["started"], reverse=True)


def by_hex(uid):
    try:
        return sessions.get(int(uid, 16))
    except ValueError:
        return None


async def handler(ws):
    path = ws.request.path.split("?", 1)[0]
    if path == "/watch":
        await watch(ws)
    else:
        await ingest(ws)


async def watch(ws):
    """A live viewer. It gets {"type": "sessions"} right away (and again when that changes),
    sends {"watch": "<uid>"}, then gets {"type": "manifest"} (again when it changes) and binary
    GMLC chunks (live/race.py) holding every frame newer than what it already has, ~5 a second.
    Finished chunks it fetches over HTTPS (/s/<uid>/chunks/<name>)."""
    watchers[ws] = None
    try:
        await ws.send(json.dumps({"type": "sessions", "sessions": live_sessions()}))
        async for msg in ws:
            if not isinstance(msg, str):
                continue
            try:
                want = json.loads(msg).get("watch")
            except (ValueError, AttributeError):
                continue
            s = by_hex(want) if isinstance(want, str) else None
            watchers[ws] = s.meta["uid"] if s else None
            if s:
                await ws.send(json.dumps({"type": "manifest", "manifest": s.manifest()}, separators=(",", ":")))
                for _, chunk in s.tail(-1e9):
                    await ws.send(chunk)
    except ConnectionClosed:
        pass
    finally:
        watchers.pop(ws, None)


async def pusher():
    """New frames to live viewers every PUSH_EVERY, the manifest when it changed, the list of
    live sessions when that changed. Each push is encoded once per session, not per viewer."""
    sent = {}       # uid -> session time of the newest frame pushed
    versions = {}   # uid -> (manifest version pushed, when)
    listed = None
    while True:
        await asyncio.sleep(PUSH_EVERY)
        try:
            now = time.time()
            live = live_sessions()
            text = json.dumps({"type": "sessions", "sessions": [{k: v for k, v in r.items() if k != "t"} for r in live]})
            if text != listed:
                listed = text
                for ws in list(watchers):
                    asyncio.create_task(send(ws, text))
            wanted = set(watchers.values()) - {None}
            for uid in wanted:
                s = by_hex(uid)
                if s is None:
                    continue
                to = [ws for ws, u in list(watchers.items()) if u == uid]
                v, at = versions.get(uid, (-1, 0.0))
                if s.version != v and now - at >= MANIFEST_EVERY:
                    versions[uid] = (s.version, now)
                    msg = json.dumps({"type": "manifest", "manifest": s.manifest()}, separators=(",", ":"))
                    for ws in to:
                        asyncio.create_task(send(ws, msg))
                newest = s.newest()
                if newest is None:
                    continue
                if uid not in sent or sent[uid] > newest:   # first push, or a flashback / restart
                    sent[uid] = newest
                    continue
                for t, chunk in s.tail(sent[uid]):
                    sent[uid] = max(sent[uid], t)
                    for ws in to:
                        asyncio.create_task(send(ws, chunk))
            for uid in list(sent):
                if uid not in wanted:
                    del sent[uid]
        except Exception as ex:
            note(f"live push: {ex}")


async def send(ws, msg):
    try:
        await asyncio.wait_for(ws.send(msg), 5)
    except Exception:
        pass  # a slow or gone viewer: the next push (or its reconnect) catches up


async def ingest(ws):
    state["uplink"] = True
    last = {}
    try:
        async for msg in ws:
            if isinstance(msg, str) or len(msg) < MESSAGE.size:
                continue
            stream, seq, _ = MESSAGE.unpack_from(msg)
            if seq > last.get(stream, 0):
                try:
                    take(zlib.decompress(msg[MESSAGE.size:], -15))
                except Exception as ex:
                    note(f"bad batch {seq}: {ex}")
                last[stream] = seq
                state["last_batch"] = time.time()
                state["batches"] += 1
            await ws.send(f"ack {seq}")
    except ConnectionClosed:
        pass  # the PC went away (shutdown, network): it resends what wasn't acked
    finally:
        state["uplink"] = False


def status():
    now = time.time()
    return {"ok": True, "uptime_s": round(now - state["started"]), "uplink_connected": state["uplink"],
            "last_batch_s_ago": round(now - state["last_batch"], 1) if state["last_batch"] else None,
            "batches": state["batches"], "errors": state["errors"], "maps_copied": state["maps"],
            "uplink_rejected": state["rejected"],
            "last_rejected_s_ago": round(now - state["last_rejected"], 1) if state["last_rejected"] else None,
            "live_viewers": len(watchers), "live": [r["uid"] for r in live_sessions()],
            "sessions": [{"uid": s.meta["uid"], "track": s.manifest()["track"], "session": s.manifest()["session"],
                          "chunks": len(s.meta["chunks"]), "cars": len(s.meta["participants"]),
                          "finished": s.meta["finished"], "published": s.meta["published"],
                          "published_in_s": s.meta.get("published_in_s"), "incidents": s.meta.get("incidents")}
                         for s in sessions.values()]}


def reply(status_code, body, kind="application/json", cache="no-store"):
    if isinstance(body, str):
        body = body.encode()
    return Response(status_code, "OK" if status_code == 200 else "Not Found", Headers([
        ("Content-Type", kind), ("Content-Length", str(len(body))), ("Cache-Control", cache),
        ("Access-Control-Allow-Origin", "*")]), body)


def session_file(path, query):
    """/s/<uid>/manifest.json, /s/<uid>/chunks/<name>, /s/<uid>/tail?after=<t>"""
    parts = path.split("/")
    s = by_hex(parts[2]) if len(parts) >= 4 else None
    if s is None:
        return reply(404, '{"error":"no such live session"}')
    if parts[3:] == ["manifest.json"]:
        return reply(200, json.dumps(s.manifest(), separators=(",", ":")))
    if len(parts) == 5 and parts[3] == "chunks" and parts[4] in s.meta["chunks"]:
        with open(os.path.join(s.dir, "chunks", parts[4]), "rb") as f:
            return reply(200, f.read(), "application/octet-stream", "public, max-age=3600")
    if parts[3:] == ["tail"]:
        try:
            after = float(dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv).get("after", "-1e9"))
        except ValueError:
            after = -1e9
        out = s.tail(after)
        return reply(200, out[0][1] if out else b"", "application/octet-stream")  # one chunk's worth per poll
    return reply(404, '{"error":"not found"}')


async def process_request(connection, request):
    path, _, query = request.path.partition("?")
    if path == "/ingest":
        auth = request.headers.get("Authorization", "")
        if not TOKEN or not hmac.compare_digest(auth, f"Bearer {TOKEN}"):
            state["rejected"] += 1
            state["last_rejected"] = time.time()
            note("uplink refused: wrong or missing live token")
            return connection.respond(401, "wrong or missing ingest token\n")
        return None  # carry on with the WebSocket handshake
    if path == "/watch":
        return None
    if path == "/status":
        return reply(200, json.dumps(status(), indent=1) + "\n")
    if path == "/live.json":
        return reply(200, json.dumps({"sessions": live_sessions()}))
    if path.startswith("/s/"):
        return session_file(path, query)
    if path in ("/", "/health"):
        return connection.respond(200, "GreMi Gang live race server\n")
    return connection.respond(404, "not found\n")


def bundle(s):
    """The full replay (incidents + verdicts) from the race's recording; None: publish the live chunks."""
    try:
        return final.build(s, DATA_DIR)
    except Exception as ex:
        note(f"bundling {s.meta['uid']} failed, publishing the live chunks: {ex}")
        return None


async def publish_race(s):
    try:
        t = time.time()
        built = await asyncio.to_thread(bundle, s)
        m = await asyncio.to_thread(publish.publish, s, None, built)
        log.info("bundled and published in %.1f s", time.time() - t)
        s.meta["published"] = True
        s.meta["published_in_s"] = round(time.time() - s.meta.get("finished_at", t), 1)
        s.meta["incidents"] = len(m.get("incidents", [])) if built else None
        s.dirty = True
        s.flush()
        log.info("published %s %s %s", m["uid"], m["track"], m["session"])
    except Exception as ex:
        s.publish_failed = time.time()  # try again in a minute
        note(f"publishing {s.meta['uid']} failed: {ex}")


async def sync_maps():
    """New map pyramids from GitHub to R2 (live/maps.py), at most once an hour."""
    try:
        done = await asyncio.to_thread(maps.sync, DATA_DIR)
        state["maps"] = sorted(set(state["maps"]) | set(done))
        if done:
            log.info("map pyramid copied to R2: %s", ", ".join(done))
    except Exception as ex:
        note(f"copying the map pyramid failed: {ex}")


async def housekeeping():
    last_clean = last_warm = 0.0
    maps_task = warm_task = None
    while True:
        await asyncio.sleep(2)
        now = time.time()
        for uid, s in list(sessions.items()):
            try:
                if not s.meta["finished"] and now - s.last_seen > IDLE_FINISH:
                    s.finish()
                elif s.dirty:
                    s.flush()
                if (s.meta["finished"] and s.is_race() and s.meta["chunks"] and not s.meta["published"]
                        and now - getattr(s, "publish_failed", 0) > 60):
                    await publish_race(s)
                if s.meta["finished"] and now - s.last_seen > FORGET_AFTER and (s.meta["published"] or not s.is_race()):
                    del sessions[uid]
            except Exception as ex:
                note(f"session {uid:016x}: {ex}")
        if now - last_warm > final.PULL_EVERY and (warm_task is None or warm_task.done()):
            # keep the race-stats code (incidents, verdicts) fresh, so a finished race never waits on git
            last_warm = now
            warm_task = asyncio.create_task(asyncio.to_thread(warm))
        if now - last_clean > 3600:
            last_clean = now
            clean(now)
            if maps_task is None or maps_task.done():
                maps_task = asyncio.create_task(sync_maps())


def warm():
    try:
        final.modules(DATA_DIR)
    except Exception as ex:
        note(f"race-stats code for incidents not ready: {ex}")


def clean(now):
    """Practice, qualifying and anything else that isn't a race only stays a few days."""
    root = os.path.join(DATA_DIR, "sessions")
    for name in os.listdir(root) if os.path.isdir(root) else []:
        d = os.path.join(root, name)
        try:
            meta = json.load(open(os.path.join(d, "meta.json")))
        except (OSError, ValueError):
            meta = {}
        if meta.get("session_type") in (15, 16, 17):
            continue
        if now - os.path.getmtime(d) > KEEP_OTHER_DAYS * 86400 and int(name, 16) not in sessions:
            shutil.rmtree(d, ignore_errors=True)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(os.path.join(DATA_DIR, "sessions"), exist_ok=True)
    if not TOKEN:
        note("INGEST_TOKEN is not set, the PC can't connect")
    try:
        await asyncio.to_thread(publish.allow_site, publish.client())
    except Exception as ex:
        note(f"couldn't set the R2 CORS rule (set it in Cloudflare by hand): {ex}")
    async with serve(handler, "127.0.0.1", PORT, process_request=process_request, max_size=1 << 20,
                     ping_interval=20, ping_timeout=40):
        log.info("listening on 127.0.0.1:%d", PORT)
        push = asyncio.create_task(pusher())
        await housekeeping()
        push.cancel()


if __name__ == "__main__":
    asyncio.run(main())
