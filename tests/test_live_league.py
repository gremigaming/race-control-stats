import asyncio
import hashlib
import io
import json
import os
import tempfile
import unittest

from live import league, publish

try:
    from live import server
except ModuleNotFoundError:
    server = None


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    async def recv(self):
        return self.messages.pop(0)

    async def send(self, message):
        self.sent.append(message)


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.order = []

    def put_object(self, **kwargs):
        body = kwargs["Body"]
        self.objects[kwargs["Key"]] = body.read() if hasattr(body, "read") else body
        self.order.append(kwargs["Key"])

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}


class FakeConnection:
    def respond(self, status, body):
        return status, body


class FakeRequest:
    def __init__(self, path, token):
        self.path = path
        self.headers = {"Authorization": token}


class ArtifactUploadTest(unittest.IsolatedAsyncioTestCase):
    @unittest.skipIf(server is None, "websockets is not installed")
    async def test_publish_endpoint_requires_the_existing_live_token(self):
        old_token = server.TOKEN
        server.TOKEN = "known-secret"
        try:
            accepted = await server.process_request(FakeConnection(), FakeRequest("/publish", "Bearer known-secret"))
            refused = await server.process_request(FakeConnection(), FakeRequest("/publish", "Bearer wrong"))
            self.assertIsNone(accepted)
            self.assertEqual(refused[0], 401)
        finally:
            server.TOKEN = old_token

    async def test_upload_is_atomic_and_retries_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = b'{"classification-data":[]}'
            header = json.dumps({"type": "artifact", "kind": "race", "day": "2026_11_02",
                                 "name": "Race_Monza_2026_11_02_20_00_00.json", "size": len(raw)})
            first = FakeWebSocket([header, raw])
            await league.receive(first, tmp)
            second = FakeWebSocket([header, raw])
            await league.receive(second, tmp)
            target = os.path.join(tmp, "league", "incoming", "races", "2026_11_02",
                                  "Race_Monza_2026_11_02_20_00_00.json")
            with open(target, "rb") as fh:
                self.assertEqual(fh.read(), raw)
            self.assertEqual(json.loads(first.sent[0])["status"], "stored")
            self.assertEqual(json.loads(second.sent[0])["status"], "duplicate")

    async def test_rejects_path_traversal_and_unsupported_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            header = json.dumps({"type": "artifact", "kind": "race", "day": "2026_11_02",
                                 "name": "..\\secret.json", "size": 4})
            ws = FakeWebSocket([header])
            await league.receive(ws, tmp)
            self.assertFalse(json.loads(ws.sent[0])["ok"])
            self.assertFalse(os.path.exists(os.path.join(tmp, "secret.json")))

    async def test_late_telemetry_and_encrypted_capture_are_kept_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind, name in (("telemetry", "2026-11-02_20-00-00_Monza_Race.gmtel.gz"),
                               ("classification", "2026-11-02_20-00-00_Monza_Race.gmcls")):
                raw = b"late artifact"
                header = json.dumps({"type": "artifact", "kind": kind, "day": "2026_11_02",
                                     "name": name, "size": len(raw)})
                ws = FakeWebSocket([header, raw])
                await league.receive(ws, tmp)
                folder = "classification" if kind == "classification" else ""
                path = os.path.join(tmp, "telemetry-cache", folder, name)
                with open(path, "rb") as fh:
                    self.assertEqual(fh.read(), raw)
                self.assertEqual(json.loads(ws.sent[0])["status"], "stored")


class LeaguePublisherTest(unittest.TestCase):
    def test_index_is_published_last_and_unchanged_objects_are_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "league", "build", "public", "league")
            os.makedirs(os.path.join(folder, "sessions"))
            name = "Race_Monza_2026_11_02_20_00_00.json"
            data = b'{"session-info":{},"classification-data":[]}'
            with open(os.path.join(folder, "sessions", name), "wb") as fh:
                fh.write(data)
            revision = hashlib.sha256(data).hexdigest()
            with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as fh:
                json.dump({"files": [name], "defaultDriver": "ttv/gremi_gaming", "revisions": {name: revision}}, fh)

            s3 = FakeS3()
            first = publish.publish_league(folder, pending=True, s3=s3)
            self.assertEqual(s3.order[-1], "league/index.json")
            self.assertTrue(first["pending"])
            self.assertIn(f"sessions/{name}", first["assetPaths"])
            object_keys = [key for key in s3.order if key != "league/index.json"]

            second = publish.publish_league(folder, pending=False, s3=s3)
            self.assertFalse(second["pending"])
            self.assertEqual(s3.order[-1], "league/index.json")
            self.assertEqual([key for key in s3.order if key != "league/index.json"], object_keys)

    def test_provisional_generation_keeps_prior_analysis_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "league", "build", "public", "league")
            os.makedirs(folder)
            with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as fh:
                json.dump({"files": [], "revisions": {}}, fh)
            s3 = FakeS3()
            old = {"files": [], "assetPaths": {"safety.json": "objects/old/safety.json",
                                                   "replays/abc.json": "objects/old/replays/abc.json"}}
            s3.objects["league/index.json"] = json.dumps(old).encode()
            index = publish.publish_league(folder, pending=True, s3=s3)
            self.assertEqual(index["assetPaths"]["safety.json"], old["assetPaths"]["safety.json"])
            self.assertEqual(index["assetPaths"]["replays/abc.json"], old["assetPaths"]["replays/abc.json"])


class PreemptTest(unittest.TestCase):
    """A new race save stops the slow analysis so its results publish at once (GreMi 2026-11-22)."""

    def setUp(self):
        league._race_in.clear()

    def tearDown(self):
        league._race_in.clear()

    def test_new_race_stops_an_abortable_step_with_its_helpers(self):
        import sys
        import threading
        import time
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "helper-still-running")
            # the step starts a helper (like the fault engine's process pool) that would write later
            helper = os.path.join(tmp, "helper.py")
            with open(helper, "w") as fh:
                fh.write(f"import time\ntime.sleep(1.5)\nopen({marker!r}, 'w').close()\n")
            code = f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, {helper!r}])\ntime.sleep(30)\n"
            threading.Timer(0.3, league._race_in.set).start()
            start = time.time()
            with self.assertRaises(league.Preempted):
                league._run([sys.executable, "-c", code], tmp, os.environ.copy(), "fault engine", abortable=True)
            self.assertLess(time.time() - start, 5)
            time.sleep(2)
            self.assertFalse(os.path.exists(marker))

    def test_other_steps_finish_even_when_a_race_arrives(self):
        import sys
        league._race_in.set()
        with tempfile.TemporaryDirectory() as tmp:
            league._run([sys.executable, "-c", "import time; time.sleep(1)"], tmp, os.environ.copy(), "safety rating")

    def test_timeout_still_raises(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                league._run([sys.executable, "-c", "import time; time.sleep(30)"], tmp, os.environ.copy(), "x", timeout=1)

    def test_a_race_upload_flags_the_build_but_telemetry_does_not(self):
        async def upload(tmp, kind, name):
            raw = b"{}"
            header = json.dumps({"type": "artifact", "kind": kind, "day": "2026_11_22", "name": name, "size": len(raw)})
            await league.receive(FakeWebSocket([header, raw]), tmp)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(upload(tmp, "telemetry", "2026-11-22_20-00-00_Monza_Race.gmtel.gz"))
            self.assertFalse(league._race_in.is_set())
            asyncio.run(upload(tmp, "race", "Race_Monza_2026_11_22_20_00_00.json"))
            self.assertTrue(league._race_in.is_set())

    def test_preempted_build_publishes_again_without_the_error_pause(self):
        calls, notes = [], []
        def rebuild(data_dir, status, note):
            calls.append(1)
            if len(calls) == 1:
                raise league.Preempted("fault engine stopped for a new race save")
            raise asyncio.CancelledError
        async def go():
            old = league.rebuild
            league.rebuild = rebuild
            try:
                status = {}
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(league.publish_worker("/nonexistent", status, notes.append), 10)
            finally:
                league.rebuild = old
        asyncio.run(go())
        self.assertEqual(len(calls), 2)
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
