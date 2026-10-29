"""Copy the raster map pyramid into Cloudflare R2, where the live viewer reads it:

    maps/index.json                  {track: {px, levels, tile0, tiles: {z: [[x, y], ...]}}}
    maps/<track>/<z>/<x>_<y>.webp    1024 px tiles, tile size tile0 / 2**z metres, x right, z down

The pyramid is rendered on GitHub (race-stats-maps, pyramid workflow), which attaches one
pyramid-<track>.tar to the public "pyramid" release. This only downloads a tar when it is
newer than the copy already in R2 (DATA_DIR/maps.json), so it is cheap to run every hour.
"""

import json
import os
import tarfile
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from live import publish

RELEASE = "https://api.github.com/repos/gremigaming/race-stats-maps/releases/tags/pyramid"
PREFIX = "maps"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "race-live"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def upload_track(s3, root, track):
    """Upload one extracted track folder; returns its index.json."""
    files = []
    for dirpath, _, names in os.walk(os.path.join(root, track)):
        files += [os.path.join(dirpath, n) for n in names if n.endswith(".webp")]

    def put(path):
        key = f"{PREFIX}/{os.path.relpath(path, root).replace(os.sep, '/')}"
        with open(path, "rb") as f:
            s3.put_object(Bucket=publish.BUCKET, Key=key, Body=f.read(), ContentType="image/webp",
                          CacheControl="public, max-age=86400")

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(put, files))
    with open(os.path.join(root, track, "index.json")) as f:
        return json.load(f)


def sync(data_dir, s3=None, release=None, download=None):
    """Bring R2 up to date with the release. Returns the tracks it copied."""
    release = release or fetch_json(RELEASE)
    state_path = os.path.join(data_dir, "maps.json")
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {"tracks": {}, "index": {}}
    todo = [a for a in release.get("assets", [])
            if a["name"].startswith("pyramid-") and a["name"].endswith(".tar")
            and state["tracks"].get(a["name"][8:-4]) != a["updated_at"]]
    if not todo:
        return []
    s3 = s3 or publish.client()
    download = download or urllib.request.urlretrieve
    done = []
    for a in todo:
        track = a["name"][8:-4]
        with tempfile.TemporaryDirectory(dir=data_dir) as tmp:
            tar = os.path.join(tmp, a["name"])
            download(a["browser_download_url"], tar)
            with tarfile.open(tar) as t:
                t.extractall(tmp, filter="data")
            state["index"][track] = upload_track(s3, tmp, track)
        state["tracks"][track] = a["updated_at"]
        publish.put_json(s3, f"{PREFIX}/index.json", state["index"], 300)
        with open(state_path, "w") as f:
            json.dump(state, f)
        done.append(track)
    return done
