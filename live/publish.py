"""Copy a finished race to Cloudflare R2 (bucket race-replays, served at
https://replays.gremigaming.com), where the race-stats viewer reads it:

    index.json               every published race, newest first
    <uid>/manifest.json      track, session, cars (shown names only), events, chunk list
    <uid>/chunks/00012.bin   10 s of every car (live/race.py), stored gzip'd

Keys come from /etc/race-live.env (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY), typed in by live/bootstrap.sh.
"""

import json
import os

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


def publish(session, s3=None):
    """Upload every chunk, the manifest and the updated index. Returns the manifest."""
    s3 = s3 or client()
    m = session.manifest()
    prefix = m["uid"]
    for name in m["chunks"]:
        with open(os.path.join(session.dir, "chunks", name), "rb") as f:
            s3.put_object(Bucket=BUCKET, Key=f"{prefix}/chunks/{name}", Body=f.read(),
                          ContentType="application/octet-stream", ContentEncoding="gzip",
                          CacheControl="public, max-age=31536000, immutable")
    put_json(s3, f"{prefix}/manifest.json", m, 60)
    try:
        index = json.loads(s3.get_object(Bucket=BUCKET, Key="index.json")["Body"].read())
    except Exception:
        index = {"races": []}
    entry = {k: m[k] for k in ("uid", "started", "track", "session", "laps")}
    entry["cars"] = len(m["cars"])
    index["races"] = [entry] + [r for r in index["races"] if r["uid"] != m["uid"]]
    index["races"].sort(key=lambda r: r["started"], reverse=True)
    put_json(s3, "index.json", index, 30)
    return m


def put_json(s3, key, value, max_age):
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(value, separators=(",", ":")).encode(),
                  ContentType="application/json", CacheControl=f"public, max-age={max_age}")
