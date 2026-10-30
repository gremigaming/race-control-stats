import asyncio
import gzip
import importlib.util
import json
import os
import shutil
import struct
import tempfile
import unittest
import zlib

from live import race

UID = 0x1234ABCD


def header(pid, t, uid=UID):
    return race.HEADER.pack(2025, 25, 1, 0, 1, pid, uid, t, 0, 0, 0, 255)


def record(kind, pid, t, body, uid=UID):
    rec = header(pid, t, uid) + body
    return bytes([kind, len(rec) & 0xFF, len(rec) >> 8]) + rec


def motion(t, cars):
    """cars: [(x, z, heading)] -> a kind 2 motion record."""
    rows = b"".join(race.FIELD_MOTION.pack(x, 1.0, z, 0, 0, 0, round(32767 * __import__("math").cos(h)),
                                           0, round(32767 * __import__("math").sin(h)), 0, 0, 0)
                    for x, z, h in cars)
    return record(2, 0, t, bytes([race.FIELD_MOTION.size]) + rows)


def telemetry(t, cars):
    """cars: [(speed, throttle, brake, steer, gear, surfaces)]"""
    rows = b"".join(race.FIELD_TELEMETRY.pack(s, th, st, br, 0, g) + bytes(surf)
                    for s, th, br, st, g, surf in cars)
    return record(2, 6, t, bytes([race.FIELD_TELEMETRY.size + 4]) + rows)


def laps(t, cars):
    """cars: [(lap_distance, lap, position)]"""
    rows = b"".join(race.FIELD_LAP.pack(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, d, d, 0, p, lap, *([0] * 12))
                    for d, lap, p in cars)
    return record(2, 2, t, bytes([race.FIELD_LAP.size]) + rows)


def participants(cars):
    """cars: [(name, ai, team, number, public)] in the 2025 layout (57 bytes per car)."""
    rows = b""
    for name, ai, team, number, public in cars:
        r = bytearray(57)
        r[0], r[3], r[5] = ai, team, number
        r[7:7 + len(name)] = name.encode()
        r[39] = public
        rows += bytes(r)
    rows += bytes(57 * (22 - len(cars)))
    return record(0, 4, 0.0, bytes([len(cars)]) + rows)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def feed(self, s, *recs):
        for kind, head, body in race.records(b"".join(recs)):
            s.add(kind, head, body)

    def test_chunks_hold_every_car_with_inputs(self):
        s = race.Session(self.tmp.name, UID)
        self.feed(s,
                  record(0, 1, 0.0, bytes([0, 30, 20, 5, 0, 0, 15, 11])),  # race at Monza, 5 laps
                  participants([("GreMi", 0, 3, 7, 1), ("Hidden Guy", 0, 1, 44, 0)]),
                  laps(0.0, [(120.5, 1, 1), (100.0, 1, 2)]),
                  telemetry(0.0, [(250, 1.0, 0.0, -0.5, 7, [0, 0, 0, 0]), (240, 0.5, 0.2, 0.1, 6, [7, 7, 0, 0])]),
                  motion(0.05, [(10.0, 20.0, 0.5), (12.0, 22.0, -1.0)]),
                  motion(5.0, [(11.0, 21.0, 0.5), (13.0, 23.0, -1.0)]),
                  record(0, 3, 6.0, b"COLL" + bytes([0, 1])),
                  record(0, 3, 6.1, b"BUTN" + bytes(4)),
                  motion(10.2, [(14.0, 24.0, 0.5), (15.0, 25.0, -1.0)]))
        # crossing 10 s wrote the first chunk
        self.assertEqual(s.meta["chunks"], ["00000.bin"])
        t0, t, cols = race.decode_chunk(gzip.decompress(open(os.path.join(s.dir, "chunks", "00000.bin"), "rb").read()))
        self.assertEqual(t0, 0.0)
        self.assertEqual(len(t), 2)
        self.assertEqual(cols["cars"], 2)
        self.assertAlmostEqual(cols["x"][2], 11.0)
        self.assertAlmostEqual(cols["z"][3], 23.0)
        self.assertAlmostEqual(cols["heading"][0], 0.5, places=3)
        self.assertEqual(cols["speed"][:2], [250, 240])
        self.assertEqual(cols["throttle"][:2], [255, 128])
        self.assertEqual(cols["steer"][:2], [-64, 13])
        self.assertEqual(cols["offtrack"][:2], [0, 3])
        self.assertEqual(cols["position"][:2], [1, 2])
        self.assertAlmostEqual(cols["lap_distance"][0], 120.5)

        s.finish()
        m = s.manifest()
        self.assertEqual((m["track"], m["session"], m["laps"]), ("Monza", "Race", 5))
        self.assertTrue(s.is_race())
        self.assertEqual(m["chunks"], ["00000.bin", "00001.bin"])
        self.assertEqual(m["events"], [{"t": 6.0, "code": "COLL", "a": 0, "b": 1}])
        # hidden-name players never reach the public manifest by name
        self.assertEqual(m["cars"]["0"]["name"], "GreMi")
        self.assertEqual(m["cars"]["1"]["name"], "Car #44")
        self.assertNotIn("Hidden Guy", json.dumps(m))

    def test_session_survives_a_restart(self):
        s = race.Session(self.tmp.name, UID)
        self.feed(s, motion(1.0, [(1.0, 2.0, 0.0)]), motion(11.0, [(1.0, 2.0, 0.0)]))
        s.flush()
        again = race.Session(self.tmp.name, UID)
        self.assertEqual(again.meta["chunks"], ["00000.bin", "00001.bin"])

    def test_late_rows_never_overwrite_a_written_chunk(self):
        s = race.Session(self.tmp.name, UID)
        self.feed(s, motion(1.0, [(1.0, 2.0, 0.0)]), motion(2.0, [(1.0, 2.0, 0.0)]),
                  motion(11.0, [(1.0, 2.0, 0.0)]), motion(3.0, [(9.0, 9.0, 0.0)]))
        _, t, _ = race.decode_chunk(gzip.decompress(open(os.path.join(s.dir, "chunks", "00000.bin"), "rb").read()))
        self.assertEqual(len(t), 2)


class GhostTest(unittest.TestCase):
    def test_cars_driving_through_each_other_without_contact_are_ghosted(self):
        car = lambda x, z: (x, z, 0.0, 0.0, 200)  # noqa: E731  (x, z, y, heading, speed)
        self.assertEqual(race.overlaps({0: car(0, 0), 1: car(1, 0.5), 2: car(0, 2.5)}), [(0, 1)])
        self.assertEqual(race.overlaps({0: car(0, 0), 1: car(6, 0)}), [])  # nose to tail: real cars
        # car 1 goes through cars 0 and 2 (no contact): car 1 is the ghost; 3 and 4 touched for real
        hits = [(10.0, 0, 1), (10.1, 0, 1), (20.0, 1, 2), (20.1, 1, 2), (30.0, 3, 4), (30.1, 3, 4)]
        g = race.ghosts(hits, [{"t": 30.05, "a": 4, "b": 3}])
        self.assertEqual(g, {"1": [[9.0, 11.1], [19.0, 21.1]]})
        # one stray frame is not enough
        self.assertEqual(race.ghosts([(5.0, 0, 1)], []), {})


class TimingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_rows_are_timed_by_game_frames_and_carry_the_game_interval(self):
        s = race.Session(self.tmp.name, UID)

        def lap_row(t, last_ms, gap_ms, grid, status):
            row = race.FIELD_LAP.pack(last_ms, 0, 0, 0, 0, 0, gap_ms % 60000, gap_ms // 60000, 0, 0, 50.0, 50.0, 0,
                                      2, 1, *([0] * 9), grid, 0, status)
            return record(2, 2, t, bytes([race.FIELD_LAP.size]) + row * 2)

        recs = [lap_row(0.0, 0, 1234, 3, 2),
                telemetry(0.0, [(216, 1.0, 0.0, 0.0, 7, [0, 0, 0, 0])] * 2)]
        # 216 km/h = 1 m per game frame; rows every 6 frames, session time jittered by up to 15 ms
        jitter = [0, 0.015, -0.01, 0.012, -0.015, 0.0, 0.01]
        for k, j in enumerate(jitter):
            recs.append(motion(k * 0.1 + j, [(k * 6.0, 0.0, 0.0), (k * 6.0, 5.0, 0.0)]))
        recs.append(motion(0.6 + 0.004, [(36.0, 0.0, 0.0), (36.0, 5.0, 0.0)]))  # repeats the last frame
        recs += [lap_row(0.7, 91500, 1234, 3, 2), lap_row(0.8, 91500, 1234, 3, 3)]
        for kind, head, body in race.records(b"".join(recs)):
            s.add(kind, head, body)
        ts = [t for t, _ in s.frames]
        self.assertEqual(len(ts), len(jitter))
        steps = [b - a for a, b in zip(ts, ts[1:])]
        self.assertLess(max(steps) - min(steps), 0.01)
        s.flush()
        _, _, cols = race.decode_chunk(gzip.decompress(open(os.path.join(s.dir, "chunks", "00000.bin"), "rb").read()))
        self.assertEqual(cols["gap_front"][:2], [1230, 1230])
        d = s.meta["timing"]["0"]
        self.assertEqual(d["grid"], 3)
        self.assertEqual(d["laps"], [[1, 0.7, 91500]])
        self.assertEqual([st for _, st in d["status"]], [2, 3])


@unittest.skipUnless(importlib.util.find_spec("websockets"), "websockets not installed")
class ServerTest(unittest.TestCase):
    def test_uplink_batches_are_acked_and_stored(self):
        from websockets.asyncio.client import connect

        from live import server

        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                server.DATA_DIR, server.TOKEN = tmp, "secret"
                server.sessions.clear()
                async with server.serve(server.ingest, "127.0.0.1", 0, process_request=server.process_request) as srv:
                    port = srv.sockets[0].getsockname()[1]
                    with self.assertRaises(Exception):
                        async with connect(f"ws://127.0.0.1:{port}/ingest"):
                            pass
                    async with connect(f"ws://127.0.0.1:{port}/ingest",
                                       additional_headers={"Authorization": "Bearer secret"}) as ws:
                        c = zlib.compressobj(6, zlib.DEFLATED, -15)
                        body = c.compress(motion(1.0, [(1.0, 2.0, 0.0)])) + c.flush()
                        await ws.send(server.MESSAGE.pack(7, 1, 0) + body)
                        self.assertEqual(await ws.recv(), "ack 1")
                        await ws.send(server.MESSAGE.pack(7, 1, 0) + body)  # a resend is ignored
                        self.assertEqual(await ws.recv(), "ack 1")
                    self.assertEqual(len(server.sessions[UID].frames), 1)
                    st = server.status()
                    self.assertEqual(st["batches"], 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()


class MapsTest(unittest.TestCase):
    def test_new_pyramids_go_to_r2_once(self):
        import io
        import tarfile
        import tempfile

        from live import maps

        class S3:
            def __init__(self):
                self.keys = {}

            def put_object(self, Bucket, Key, Body, **kw):
                self.keys[Key] = Body

        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as src:
            tar = os.path.join(src, "pyramid-monaco.tar")
            with tarfile.open(tar, "w") as t:
                for name, body in (("monaco/4/0_-12.webp", b"RIFF"), ("monaco/index.json", b'{"px":1024}')):
                    info = tarfile.TarInfo(name)
                    info.size = len(body)
                    t.addfile(info, io.BytesIO(body))
            release = {"assets": [{"name": "pyramid-monaco.tar", "updated_at": "2026-10-29T11:00:00Z",
                                   "browser_download_url": tar}]}
            s3 = S3()
            copy = lambda url, path: shutil.copy(url, path)
            self.assertEqual(maps.sync(data, s3, release, copy), ["monaco"])
            self.assertEqual(s3.keys["maps/monaco/4/0_-12.webp"], b"RIFF")
            self.assertEqual(json.loads(s3.keys["maps/index.json"]), {"monaco": {"px": 1024}})
            self.assertEqual(maps.sync(data, s3, release, copy), [])
