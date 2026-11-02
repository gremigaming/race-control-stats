"""Copy a finished race to Cloudflare R2 (bucket race-replays, served at
https://replays.gremigaming.com), where the race-stats viewer reads it:

    index.json               every published race, newest first
    <uid>/manifest.json      track, session, cars (shown names only), events, chunk list
    <uid>/chunks/00012.bin   10 s of every car (live/race.py), stored gzip'd

Keys come from /etc/race-live.env (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY), typed in by live/bootstrap.sh.
"""

import concurrent.futures
import hashlib
import json
import mimetypes
import os
import posixpath
import time
import uuid

BUCKET = os.environ.get("R2_BUCKET", "race-replays")
SITE = "https://gremigaming.github.io"


def client():
    import boto3  # only on the server

    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def allow_site(s3):
    """Let the race-stats site (and local dev) fetch from the bucket. Needs a token that may
    change bucket settings; when it can't, server.py logs it and the CORS rule is set by hand."""
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": [SITE, "http://localhost:5173"], "AllowedMethods": ["GET", "HEAD"],
        "AllowedHeaders": ["*"], "MaxAgeSeconds": 86400}]})


def publish(session, s3=None, final=None):
    """Upload every chunk, the manifest and the updated index. Returns the manifest.
    final: (folder, manifest) from live/final.py, the full replay with incidents and verdicts;
    without it the live chunks and manifest go up as they are."""
    s3 = s3 or client()
    folder, m = final if final else (session.dir, session.manifest())
    prefix = m["uid"]
    for name in m["chunks"]:
        with open(os.path.join(folder, "chunks", name), "rb") as f:
            s3.put_object(Bucket=BUCKET, Key=f"{prefix}/chunks/{name}", Body=f.read(),
                          ContentType="application/octet-stream", ContentEncoding="gzip",
                          CacheControl="public, max-age=31536000, immutable")
    put_json(s3, f"{prefix}/manifest.json", m, 60)
    try:
        index = json.loads(s3.get_object(Bucket=BUCKET, Key="index.json")["Body"].read())
    except Exception:
        index = {"races": []}
    index["races"] = [index_entry(m)] + [r for r in index["races"] if r["uid"] != m["uid"]]
    for i, r in enumerate(index["races"]):
        if "weather" not in r:   # published before the latest picker stats: fill in once from its manifest
            try:
                old = json.loads(s3.get_object(Bucket=BUCKET, Key=f"{r['uid']}/manifest.json")["Body"].read())
                index["races"][i] = {**index_entry(old), **r, **race_summary(old)}
            except Exception:
                pass
    index["races"].sort(key=lambda r: r["started"], reverse=True)
    put_json(s3, "index.json", index, 30)
    return m


def index_entry(m):
    entry = {k: m[k] for k in ("uid", "started", "track", "session", "laps")}
    entry["cars"] = len(m["cars"])
    if "incidents" in m:
        entry["incidents"] = len(m["incidents"])
    return {**entry, **race_summary(m)}


def race_summary(m):
    """What the race picker shows besides track and date: who raced (shown names, no AI), the
    winner and gap to second (from the lap timing), the fastest lap, a 0-10 chaos rating
    (incidents, or game contacts when a live race has none, per car per lap) and the weather
    codes in the order they happened (0 clear .. 5 storm; more than one = it changed mid-race).
    Copy of race-stats scripts/telemetry/race_bundles.py race_summary: keep them equal."""
    cars, timing = m["cars"], m.get("timing", {})
    order = sorted((k for k in timing if k in cars and timing[k]["laps"]),
                   key=lambda k: (-len(timing[k]["laps"]), timing[k]["laps"][-1][1]))
    out = {"drivers": sorted({c["name"] for c in cars.values() if c.get("name") and not c.get("ai") and not c["name"].startswith("Car #")})}
    if order:
        first = timing[order[0]]["laps"]
        out["winner"] = cars[order[0]]["name"]
        if len(order) > 1 and len(timing[order[1]]["laps"]) == len(first):
            out["gap"] = round(timing[order[1]]["laps"][-1][1] - first[-1][1], 2)
    best = min(((lap[2], k) for k in order for lap in timing[k]["laps"] if lap[2] > 0), default=None)
    if best:
        out["fastest"] = {"name": cars[best[1]]["name"], "ms": best[0]}
    hits = len(m["incidents"]) if "incidents" in m else sum(1 for e in m["events"] if e["code"] == "COLL")
    out["chaos"] = min(10.0, round(hits / max(1, len(cars) * max(1, m["laps"])) * 15, 1))
    codes = [w[1] for w in m.get("weather") or []]
    out["weather"] = [c for i, c in enumerate(codes) if i == 0 or c != codes[i - 1]][:8]
    return out


def put_json(s3, key, value, max_age):
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(value, separators=(",", ":")).encode(),
                  ContentType="application/json", CacheControl=f"public, max-age={max_age}")


def publish_league(folder, pending=False, s3=None):
    """Publish static-compatible league JSON under league/ with immutable objects.

    A mutable no-store index points at content-hashed assets. Existing assets are
    reused across generations, so a new race doesn't upload or refetch history.
    """
    s3 = s3 or client()
    index_path = os.path.join(folder, "index.json")
    with open(index_path, encoding="utf-8") as fh:
        index = json.load(fh)
    state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(folder))), "published-assets.json")
    try:
        with open(state_path, encoding="utf-8") as fh:
            previous = json.load(fh)
    except (OSError, ValueError):
        try:
            previous = json.loads(s3.get_object(Bucket=BUCKET, Key="league/index.json")["Body"].read())
        except Exception:
            previous = {}
    previous_paths = previous.get("assetPaths", {})
    previous_hashes = previous.get("assetHashes", {})

    files = []
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, folder).replace(os.sep, "/")
            if rel != "index.json":
                files.append((rel, path))
    files.sort()

    asset_paths, asset_hashes = {}, {}
    uploads = []
    for rel, path in files:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        sha = digest.hexdigest()
        asset_hashes[rel] = sha
        if previous_hashes.get(rel) == sha and previous_paths.get(rel):
            asset_paths[rel] = previous_paths[rel]
            continue
        key = f"league/objects/{sha[:2]}/{sha}/{rel}"
        asset_paths[rel] = key.removeprefix("league/")
        uploads.append((path, key, rel))

    # A provisional generation may be published before safety/incident analysis
    # finishes. Keep the previous analysis objects addressable until replacements
    # are ready, while the new race sessions and results become visible now.
    if pending:
        for rel, old in previous_paths.items():
            if rel not in asset_paths and (rel in ("safety.json", "incidents.json", "awards.json") or rel.startswith("replays/")):
                asset_paths[rel] = old
                if rel in previous_hashes:
                    asset_hashes[rel] = previous_hashes[rel]

    def put(item):
        path, key, rel = item
        content_type = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            s3.put_object(Bucket=BUCKET, Key=key, Body=fh,
                          ContentType=content_type,
                          CacheControl="public, max-age=31536000, immutable")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(put, uploads))

    generation = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    revisions = {f"sessions/{name}": rev for name, rev in index.get("revisions", {}).items()}
    session_files = {name: asset_paths[f"sessions/{name}"] for name in index.get("files", [])
                     if f"sessions/{name}" in asset_paths}
    live_index = {**index, "generation": generation, "pending": bool(pending),
                  "publishedAt": datetime_now(), "revisions": revisions,
                  "sessionFiles": session_files, "assetPaths": asset_paths}
    body = json.dumps(live_index, separators=(",", ":")).encode()
    s3.put_object(Bucket=BUCKET, Key="league/index.json", Body=body,
                  ContentType="application/json", CacheControl="no-store, max-age=0, must-revalidate")

    state = {"generation": generation, "pending": bool(pending),
             "assetPaths": asset_paths, "assetHashes": asset_hashes}
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(state, fh, separators=(",", ":"))
    os.replace(state_path + ".tmp", state_path)
    return live_index


def datetime_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
