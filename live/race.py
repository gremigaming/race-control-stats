"""Race sessions for the Live Race Viewer: turn the racing PC's uplink records
into 10 s chunks on disk, plus a manifest the viewer reads.

The PC (uploader/TelemetryRecorder.cs in gremigaming/race-stats, class
GmLiveUplink) sends the same records as its .gmtel.gz files
(scripts/telemetry/gmtel.py there): uint8 kind, uint16 length, then
kind 0 = a whole game packet (session, participants, event, final classification),
kind 2 = game header + uint8 row size + the first row-size bytes of every car,
for motion (20 per second), car telemetry (20 per second) and lap data (4 per second).

A chunk is every car at every motion row inside 10 s of session time, gzip'd:

    "GMLC" uint8 version=2  uint8 cars  uint16 frames  float64 chunk start (session s)
    float32 t[frames]                      session time of each frame
    float32 x, z, y, lap_distance[frames * cars]   metres (x right, z down on the maps)
    int16   heading[frames * cars]         atan2(forward z, forward x) * 10000
    uint16  speed[frames * cars]           km/h * 10
    uint8   throttle, brake[frames * cars] 0..255
    int8    steer[frames * cars]           -127..127
    int8    gear[frames * cars]            -1 reverse, 0 neutral
    uint8   offtrack[frames * cars]        bit per wheel (RL, RR, FL, FR) off tarmac and kerbs
    uint8   lap, position[frames * cars]   position 0 = no car in that slot
    uint16  gap_front[frames * cars]       the game's interval to the car ahead, 10 ms units (version 2)

Frame times are the game's frame count between rows (see Session._retime), not the raw session
time, which can be ~20 ms off from when the positions were taken.

Arrays are frame-major (frame 0 car 0, frame 0 car 1, ...), little endian.
"""

import collections
import gzip
import json
import math
import os
import statistics
import struct
import time

HEADER = struct.Struct("<HBBBBBQfIIBB")  # 29 bytes, the game's packet header
FIELD_MOTION = struct.Struct("<6f6h")
FIELD_LAP = struct.Struct("<IIHBHBHBHBfff14B")
FIELD_TELEMETRY = struct.Struct("<HfffBb")
CHUNK_HEAD = struct.Struct("<4sBBHd")
CHUNK_SECONDS = 10
ON_TRACK = (0, 1)  # surface types: tarmac, rumble strip
GHOST_LON, GHOST_LAT = 3.5, 1.4  # two car centres this close (along, across): driving through each other

TRACKS = ["Melbourne", "Paul Ricard", "Shanghai", "Bahrain", "Catalunya", "Monaco", "Montreal",
          "Silverstone", "Hockenheim", "Hungaroring", "Spa", "Monza", "Singapore", "Suzuka",
          "Abu Dhabi", "Texas", "Brazil", "Austria", "Sochi", "Mexico", "Baku", "Bahrain Short",
          "Silverstone Short", "Texas Short", "Suzuka Short", "Hanoi", "Zandvoort", "Imola",
          "Portimao", "Jeddah", "Miami", "Las Vegas", "Losail"]
TRACKS += [""] * (39 - len(TRACKS)) + ["Silverstone Reverse", "Austria Reverse",
                                        "Zandvoort Reverse", "Madrid"]
SESSION_TYPES = ["Unknown", "P1", "P2", "P3", "Short practice", "Q1", "Q2", "Q3", "Short quali",
                 "One-shot quali", "Sprint shootout 1", "Sprint shootout 2", "Sprint shootout 3",
                 "Short sprint shootout", "One-shot sprint shootout", "Race", "Race 2", "Race 3",
                 "Time trial"]
RACES = {15, 16, 17}


def records(data):
    """(kind, header tuple, bytes after the header) for every record in an uplink batch."""
    pos = 0
    while pos + 3 <= len(data):
        kind, length = data[pos], data[pos + 1] | (data[pos + 2] << 8)
        rec = data[pos + 3:pos + 3 + length]
        pos += 3 + length
        if len(rec) < length or length < HEADER.size:
            break
        yield kind, HEADER.unpack_from(rec), rec[HEADER.size:]


def field_rows(b):
    """Split a kind 2 body into one bytes row per car slot."""
    size = b[0] if b else 0
    return [b[1 + i * size:1 + (i + 1) * size] for i in range((len(b) - 1) // size)] if size else []


def parse_participants(b):
    """{slot: {"name", "ai", "team", "race_number", "public"}}, packet formats 2025 and 2026."""
    n = b[0]
    per = (len(b) - 1) // 24 if (len(b) - 1) % 24 == 0 else (len(b) - 1) // 22
    name_at = 10 if per >= 60 else 7
    out = {}
    for i in range(n):
        r = b[1 + i * per:1 + (i + 1) * per]
        if len(r) < name_at + 33:
            break
        out[i] = {"name": r[name_at:name_at + 32].split(b"\0", 1)[0].decode("utf-8", "replace"),
                  "ai": r[0], "race_number": r[name_at - 2],
                  "team": struct.unpack_from("<H", r, 5)[0] if per >= 60 else r[3],
                  "public": r[name_at + 32]}
    return out


def shown_name(p):
    """The name the public viewer gets: hidden-name players (and the game's anonymous "Player") only by race number (GreMi's rule)."""
    if (p.get("ai") or p.get("public")) and p.get("name") not in (None, "", "Player"):
        return p["name"]
    return f"Car #{p.get('race_number', 0)}"


class Session:
    """One game session (session uid): keeps the newest state of every car and writes
    a chunk to disk whenever session time passes a 10 s boundary."""

    def __init__(self, root, uid):
        self.uid = uid
        self.dir = os.path.join(root, f"{uid:016x}")
        os.makedirs(os.path.join(self.dir, "chunks"), exist_ok=True)
        self.meta_path = os.path.join(self.dir, "meta.json")
        try:
            self.meta = json.load(open(self.meta_path))
        except (OSError, ValueError):
            self.meta = {"uid": f"{uid:016x}", "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "track_id": -1, "session_type": 0, "total_laps": 0, "participants": {},
                         "events": [], "chunks": [], "finished": False, "published": False}
        self.tel = {}     # slot -> (speed, throttle, brake, steer, gear, offtrack)
        self.lap = {}     # slot -> (lap_distance, lap, position, gap_front_ms)
        self.clock = None  # (frame id, corrected time, cars, raw time, frame period) of the last motion row
        self.frames = []  # (t, {slot: row tuple}) of the open chunk
        self.last_lap = {}  # slot -> last lap time (ms) seen, to spot a new one
        self.chunk = None
        self.last_seen = time.time()
        self.dirty = False

    # ------------------------------------------------------------- input

    def add(self, kind, head, body):
        self.last_seen = time.time()
        pid, t = head[5], head[7]
        if kind == 2:
            rows = field_rows(body)
            if pid == 0:
                self._motion(t, rows, head[9])
            elif pid == 6:
                self._telemetry(rows)
            elif pid == 2:
                self._laps(t, rows)
        elif kind == 0:
            if pid == 1 and len(body) >= 8:
                self.meta.update(total_laps=body[3], session_type=body[6], track_id=struct.unpack_from("<b", body, 7)[0],
                                 track_length=struct.unpack_from("<H", body, 4)[0])
                w = self.meta.setdefault("weather", [])
                if not w or w[-1][1] != body[0]:
                    w.append([round(t, 3), body[0]])
                if "tod" not in self.meta and len(body) >= 671:  # [session time, local minutes since midnight]
                    self.meta["tod"] = [round(t, 3), struct.unpack_from("<I", body, 667)[0]]
                self.dirty = True
            elif pid == 4 and body:
                parts = parse_participants(body)
                if parts and {str(k): v for k, v in parts.items()} != self.meta["participants"]:
                    self.meta["participants"] = {str(k): v for k, v in parts.items()}
                    self.dirty = True
            elif pid == 3 and len(body) >= 4:
                self._event(t, body)
            elif pid == 8:
                self.finish()

    def _motion(self, t, rows, frame_id):
        cars = {}
        for i, r in enumerate(rows):
            if len(r) < FIELD_MOTION.size:
                continue
            v = FIELD_MOTION.unpack_from(r)
            if v[0] == v[1] == v[2] == 0:
                continue  # empty slot
            cars[i] = (v[0], v[2], v[1], math.atan2(v[8], v[6]))
        if not cars:
            return
        t = self._retime(t, cars, frame_id)
        if t is None:
            return
        index = int(t // CHUNK_SECONDS)
        if self.chunk is not None and index < self.chunk:
            return  # a late row from before the open chunk: its chunk is already on disk
        if self.chunk is not None and index != self.chunk:
            self.flush()
        self.chunk = index
        row = {i: c + self.tel.get(i, (0, 0, 0, 0, 0, 0)) + self.lap.get(i, (0.0, 0, 0, 0)) for i, c in cars.items()}
        self.frames.append((t, row))
        for a, b in overlaps(row):
            self.meta.setdefault("ghost_hits", []).append([round(t, 2), a, b])

    def _retime(self, t, cars, frame_id):
        """Time a motion row by the game frames since the last one (same as retime in race-stats
        scripts/telemetry/race_bundles.py); None for a row that repeats the last frame."""
        speed = {i: self.tel.get(i, (0,))[0] for i in cars}
        c = self.clock
        tc = t
        period = c[4] if c else 1 / 60
        if c:
            est = [math.hypot(v[0] - c[2][i][0], v[1] - c[2][i][1]) / (speed[i] / 3.6) * 60
                   for i, v in cars.items() if i in c[2] and speed[i] > 80]
            df = round(statistics.median(est)) if len(est) >= 2 else frame_id - c[0]
            if df == 0 and t - c[3] < 0.1:
                return None
            if 0 < df <= 120 and 0 < t - c[3] < 3:
                period += 0.002 * (min(max((t - c[3]) / df, 0.008), 0.03) - period)
                tc = c[1] + df * period
                tc += 0.05 * (t - tc)
                if abs(tc - t) > 0.1 or tc <= c[1]:
                    tc = t
        self.clock = (frame_id, tc, {i: v[:2] for i, v in cars.items()}, t, period)
        return round(tc, 4)

    def _telemetry(self, rows):
        for i, r in enumerate(rows):
            if len(r) < FIELD_TELEMETRY.size:
                continue
            v = FIELD_TELEMETRY.unpack_from(r)
            off = 0
            if len(r) >= FIELD_TELEMETRY.size + 4:
                for w, s in enumerate(r[-4:]):
                    if s not in ON_TRACK:
                        off |= 1 << w
            self.tel[i] = (v[0], v[1], v[3], v[2], v[5], off)

    def _laps(self, t, rows):
        for i, r in enumerate(rows):
            if len(r) < FIELD_LAP.size:
                continue
            v = FIELD_LAP.unpack_from(r)
            if v[13]:
                self.lap[i] = (v[10], v[14], v[13], v[6] + v[7] * 60000)
                self._timing(t, i, v)

    def _timing(self, t, slot, v):
        """Grid slot, completed laps [lap, time, ms] and result status changes [time, status]
        per car (same shape as timing_of in race-stats scripts/telemetry/race_bundles.py)."""
        d = self.meta.setdefault("timing", {}).setdefault(str(slot), {"grid": 0, "laps": [], "status": []})
        last = self.last_lap.get(slot)
        if v[24] and d["grid"] != v[24]:
            d["grid"] = v[24]
            self.dirty = True
        if v[0] and last is not None and v[0] != last:
            d["laps"].append([len(d["laps"]) + 1, round(t, 2), v[0]])
            self.dirty = True
        if last is None or v[0]:
            self.last_lap[slot] = v[0]
        if not d["status"] or d["status"][-1][1] != v[26]:
            d["status"].append([round(t, 2), v[26]])
            self.dirty = True

    def _event(self, t, body):
        code = body[:4].decode("ascii", "replace")
        if code == "BUTN":
            return
        ev = {"t": round(t, 3), "code": code}
        if code == "COLL" and len(body) >= 6:
            ev.update(a=body[4], b=body[5])
        elif code == "OVTK" and len(body) >= 6:
            ev.update(a=body[4], b=body[5])
        elif code == "STLG" and len(body) >= 5:
            ev["lights"] = body[4]
        elif code in ("PENA", "RTMT", "FTLP", "SPTP", "DTSV", "SGSV") and len(body) >= 5:
            ev["car"] = body[4]
        self.meta["events"].append(ev)
        self.dirty = True

    # ------------------------------------------------------------- output

    def flush(self):
        """Write the open chunk (if any) and the manifest."""
        if self.frames:
            data = encode_chunk(self.chunk * CHUNK_SECONDS, self.frames)
            name = f"{self.chunk:05d}.bin"
            with open(os.path.join(self.dir, "chunks", name), "wb") as f:
                f.write(gzip.compress(data, 6))
            if name not in self.meta["chunks"]:
                self.meta["chunks"].append(name)
                self.meta["chunks"].sort()
            self.frames = []
            self.dirty = True
        if self.dirty:
            tmp = self.meta_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.meta, f, separators=(",", ":"))
            os.replace(tmp, self.meta_path)
            self.dirty = False

    def finish(self):
        if not self.meta["finished"]:
            self.meta["finished"] = True
            self.dirty = True
        self.flush()

    def is_race(self):
        return self.meta.get("session_type") in RACES

    def manifest(self):
        """What the public viewer reads: no raw names of hidden-name players."""
        m = self.meta
        tid = m.get("track_id", -1)
        return {"v": 1, "uid": m["uid"], "started": m["started"],
                "track": TRACKS[tid] if 0 <= tid < len(TRACKS) else "Unknown",
                "session": SESSION_TYPES[m["session_type"]] if 0 <= m.get("session_type", 0) < len(SESSION_TYPES) else "Unknown",
                "laps": m.get("total_laps", 0), "track_length": m.get("track_length", 0),
                "chunk_seconds": CHUNK_SECONDS, "chunks": m["chunks"],
                "cars": {k: {"name": shown_name(p), "team": p.get("team"), "number": p.get("race_number"),
                             "ai": bool(p.get("ai"))} for k, p in m["participants"].items()},
                "events": m["events"], "finished": m["finished"],
                "ghosts": ghosts(m.get("ghost_hits", []), [e for e in m["events"] if e["code"] == "COLL" and "a" in e]),
                "timing": {k: v for k, v in m.get("timing", {}).items() if k in m["participants"]},
                "weather": m.get("weather", []), "tod": m.get("tod")}


def overlaps(cars):
    """Pairs of cars (a, b) driving through each other in one frame: centres under GHOST_LON along
    and GHOST_LAT across, both over 30 km/h. Real cars can't get that close (~5.6 x 2 m).
    Copy of race-stats scripts/telemetry/race_bundles.py overlaps: keep them equal."""
    out = []
    items = [(i, c) for i, c in cars.items() if c[4] > 30]
    for k, (i, a) in enumerate(items):
        fx, fz = math.cos(a[3]), math.sin(a[3])
        for j, b in items[k + 1:]:
            dx, dz = b[0] - a[0], b[1] - a[1]
            if dx * dx + dz * dz > 25 or abs(b[2] - a[2]) > 2:
                continue
            if abs(dx * fx + dz * fz) < GHOST_LON and abs(dz * fx - dx * fz) < GHOST_LAT:
                out.append((i, j))
    return out


def ghosts(hits, collisions):
    """When each car was ghosted, {car: [[t0, t1], ...]}. The game sends no ghost flag (GreMi's lobbies
    ghost griefing cars: slow, wrong way, rejoining), so: two cars driving through each other with no
    game contact means one of them was ghosted. It is the one that went through more different cars
    in the race (a tie marks both). hits: [(t, a, b)] from overlaps(). Copy of race-stats scripts/telemetry/race_bundles.py ghosts."""
    contact = collections.defaultdict(list)
    for c in collisions:
        contact[frozenset((c["a"], c["b"]))].append(c["t"])
    episodes = collections.defaultdict(list)   # pair -> [[t0, t1, frames]]
    for t, a, b in sorted(hits):
        eps = episodes[frozenset((a, b))]
        if eps and t - eps[-1][1] <= 1:
            eps[-1][1] = t
            eps[-1][2] += 1
        else:
            eps.append([t, t, 1])
    kept, partners = [], collections.defaultdict(set)
    for pair, eps in episodes.items():
        for t0, t1, n in eps:
            if n >= 2 and not any(t0 - 1.5 <= ct <= t1 + 1.5 for ct in contact[pair]):
                kept.append((pair, t0, t1))
                a, b = sorted(pair)
                partners[a].add(b)
                partners[b].add(a)
    wins = collections.defaultdict(list)
    for pair, t0, t1 in kept:
        a, b = sorted(pair)
        na, nb = len(partners[a]), len(partners[b])
        for i in ([a] if na > nb else [b] if nb > na else [a, b]):
            wins[i].append([t0 - 1, t1 + 1])
    out = {}
    for i, ws in wins.items():
        merged = []
        for t0, t1 in sorted(ws):
            if merged and t0 - merged[-1][1] < 3:
                merged[-1][1] = max(merged[-1][1], t1)
            else:
                merged.append([t0, t1])
        out[str(i)] = [[round(max(t0, 0), 2), round(t1, 2)] for t0, t1 in merged]
    return out


def encode_chunk(t0, frames):
    """frames: [(t, {slot: (x, z, y, heading, speed, throttle, brake, steer, gear, offtrack,
    lap_distance, lap, position, gap_front_ms)})] -> chunk bytes (see the module doc)."""
    cars = max((max(c) for _, c in frames if c), default=-1) + 1
    n = len(frames)
    blank = (0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0, 0, 0)
    grid = [[c.get(i, blank) for i in range(cars)] for _, c in frames]
    flat = [r for row in grid for r in row]
    clamp = lambda v, lo, hi: max(lo, min(hi, v))
    out = [CHUNK_HEAD.pack(b"GMLC", 2, cars, n, float(t0)),
           struct.pack(f"<{n}f", *(t for t, _ in frames))]
    for k in (0, 1, 2, 10):
        out.append(struct.pack(f"<{len(flat)}f", *(r[k] for r in flat)))
    out.append(struct.pack(f"<{len(flat)}h", *(clamp(round(r[3] * 10000), -32768, 32767) for r in flat)))
    out.append(struct.pack(f"<{len(flat)}H", *(clamp(round(r[4] * 10), 0, 65535) for r in flat)))
    out.append(bytes(clamp(round(r[5] * 255), 0, 255) for r in flat))
    out.append(bytes(clamp(round(r[6] * 255), 0, 255) for r in flat))
    out.append(struct.pack(f"<{len(flat)}b", *(clamp(round(r[7] * 127), -127, 127) for r in flat)))
    out.append(struct.pack(f"<{len(flat)}b", *(clamp(r[8], -1, 9) for r in flat)))
    out.append(bytes(r[9] & 15 for r in flat))
    out.append(bytes(clamp(r[11], 0, 255) for r in flat))
    out.append(bytes(clamp(r[12], 0, 255) for r in flat))
    out.append(struct.pack(f"<{len(flat)}H", *(clamp(round(r[13] / 10), 0, 65535) for r in flat)))
    return b"".join(out)


def decode_chunk(data):
    """The inverse of encode_chunk, for tests and the debug page: (t0, t list, {column: list})."""
    _, version, cars, n, t0 = CHUNK_HEAD.unpack_from(data)
    pos = CHUNK_HEAD.size
    m = n * cars

    def take(fmt, count, size):
        nonlocal pos
        v = list(struct.unpack_from(f"<{count}{fmt}", data, pos))
        pos += count * size
        return v

    t = take("f", n, 4)
    cols = {k: take("f", m, 4) for k in ("x", "z", "y", "lap_distance")}
    cols["heading"] = [v / 10000 for v in take("h", m, 2)]
    cols["speed"] = [v / 10 for v in take("H", m, 2)]
    for k in ("throttle", "brake"):
        cols[k] = take("B", m, 1)
    cols["steer"] = take("b", m, 1)
    cols["gear"] = take("b", m, 1)
    for k in ("offtrack", "lap", "position"):
        cols[k] = take("B", m, 1)
    if version >= 2:
        cols["gap_front"] = [v * 10 for v in take("H", m, 2)]
    cols["cars"] = cars
    return t0, t, cols
