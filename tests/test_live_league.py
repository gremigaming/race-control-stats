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


if __name__ == "__main__":
    unittest.main()
