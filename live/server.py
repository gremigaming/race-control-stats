"""Live Race Viewer server (Hetzner, https://live.gremigaming.com behind Caddy).

    wss://live.gremigaming.com/ingest   the racing PC's uplink (Authorization: Bearer INGEST_TOKEN)
    https://live.gremigaming.com/status what is coming in, for checking without SSH (no names, no keys)

An uplink message is one binary WebSocket frame:
    uint64 stream id (new each time the uploader starts)  uint32 sequence number
    uint64 PC clock (ms)  then raw deflate of records (see live/race.py)
The server answers "ack <sequence>" so the PC can drop what arrived and resend the rest
after a dropout. Sessions are written to DATA_DIR; a race that ends (final classification,
or 2 minutes without data) goes to R2 (live/publish.py).

Run: python -m live.server (systemd unit race-live, set up by live/bootstrap.sh).
"""

import asyncio
import hmac
import json
import logging
import os
import shutil
import struct
import time
import zlib

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from live import maps, publish
from live.race import Session, records

DATA_DIR = os.environ.get("DATA_DIR", "/var/lib/race-live")
PORT = int(os.environ.get("PORT", "8100"))
TOKEN = os.environ.get("INGEST_TOKEN", "")
IDLE_FINISH = 120        # s without data: the session is over
FORGET_AFTER = 600       # s after finishing: drop it from memory
KEEP_OTHER_DAYS = 3      # practice and qualifying stay on disk this long (races stay)
MESSAGE = struct.Struct("<QIQ")

log = logging.getLogger("race-live")
sessions = {}
state = {"started": time.time(), "uplink": False, "last_batch": 0.0, "batches": 0, "errors": [], "maps": []}


def note(text):
    log.warning(text)
    state["errors"] = (state["errors"] + [f"{time.strftime('%H:%M:%S')} {text}"])[-10:]


def session(uid):
    s = sessions.get(uid)
    if s is None:
        s = sessions[uid] = Session(os.path.join(DATA_DIR, "sessions"), uid)
    return s


def take(data):
    """Feed one uplink batch into the sessions."""
    for kind, head, body in records(data):
        uid = head[6]
        if uid:
            session(uid).add(kind, head, body)


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
            "sessions": [{"uid": s.meta["uid"], "track": s.manifest()["track"], "session": s.manifest()["session"],
                          "chunks": len(s.meta["chunks"]), "cars": len(s.meta["participants"]),
                          "finished": s.meta["finished"], "published": s.meta["published"]}
                         for s in sessions.values()]}


async def process_request(connection, request):
    path = request.path.split("?", 1)[0]
    if path == "/ingest":
        auth = request.headers.get("Authorization", "")
        if not TOKEN or not hmac.compare_digest(auth, f"Bearer {TOKEN}"):
            return connection.respond(401, "wrong or missing ingest token\n")
        return None  # carry on with the WebSocket handshake
    if path == "/status":
        r = connection.respond(200, json.dumps(status(), indent=1) + "\n")
        del r.headers["Content-Type"]
        r.headers["Content-Type"] = "application/json"
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r
    if path in ("/", "/health"):
        return connection.respond(200, "GreMi Gang live race server\n")
    return connection.respond(404, "not found\n")


async def publish_race(s):
    try:
        m = await asyncio.to_thread(publish.publish, s)
        s.meta["published"] = True
        s.dirty = True
        s.flush()
        log.info("published %s %s %s", m["uid"], m["track"], m["session"])
    except Exception as ex:
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
    last_clean = 0.0
    maps_task = None
    while True:
        await asyncio.sleep(5)
        now = time.time()
        for uid, s in list(sessions.items()):
            try:
                if not s.meta["finished"] and now - s.last_seen > IDLE_FINISH:
                    s.finish()
                elif s.dirty:
                    s.flush()
                if s.meta["finished"] and s.is_race() and s.meta["chunks"] and not s.meta["published"]:
                    await publish_race(s)
                if s.meta["finished"] and now - s.last_seen > FORGET_AFTER and (s.meta["published"] or not s.is_race()):
                    del sessions[uid]
            except Exception as ex:
                note(f"session {uid:016x}: {ex}")
        if now - last_clean > 3600:
            last_clean = now
            clean(now)
            if maps_task is None or maps_task.done():
                maps_task = asyncio.create_task(sync_maps())


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
    async with serve(ingest, "127.0.0.1", PORT, process_request=process_request, max_size=1 << 20,
                     ping_interval=20, ping_timeout=40):
        log.info("listening on 127.0.0.1:%d", PORT)
        await housekeeping()


if __name__ == "__main__":
    asyncio.run(main())
