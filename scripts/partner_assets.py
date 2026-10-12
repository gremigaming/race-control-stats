"""Makes see-through overlay images for a stream partner (run by the "Partner assets" workflow).

  python3 scripts/partner_assets.py <drive folder url> <site url> <out dir> <name|name|...>

Downloads the named product photos from the partner's public Drive folder, removes
their (white) backgrounds, trims and scales them, and saves the logo candidates
found on the partner's website, all as PNG in <out dir>.
"""
import io
import os
import re
import sys
import urllib.parse
import urllib.request

import gdown
from PIL import Image
from rembg import new_session, remove

UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read(), r.headers.get("Content-Type", "")


def finish(im, out, max_side=900):
    im = im.convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if box:
        im = im.crop(box)
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    im.save(out, optimize=True)
    print(f"SAVED {out} {im.size[0]}x{im.size[1]}")


def products(folder, names, out):
    session = new_session("isnet-general-use")
    files = gdown.download_folder(folder, skip_download=True, quiet=True)
    for name in names:
        match = [f for f in files if os.path.basename(f.path) == name]
        if not match:
            print(f"MISSING {name}")
            continue
        tmp = os.path.join("/tmp", name)
        gdown.download(id=match[0].id, output=tmp, quiet=True)
        cut = remove(Image.open(tmp).convert("RGB"), session=session)
        finish(cut, os.path.join(out, os.path.splitext(name)[0].lower() + ".png"))


def logos(site, out):
    html = fetch(site)[0].decode("utf-8", "replace")
    urls = []
    for m in re.finditer(r'<img[^>]+>', html):
        tag = m.group(0)
        if "logo" in tag.lower():
            src = re.search(r'(?:src|data-src)="([^"]+)"', tag)
            if src:
                urls.append(src.group(1))
    for m in re.finditer(r'(https?:)?//[^"\'\s>]+logo[^"\'\s>]*\.(?:svg|png|webp)', html, re.I):
        urls.append(m.group(0))
    seen = set()
    for i, u in enumerate(urls):
        u = urllib.parse.urljoin(site, u.split(" ")[0])
        if u.startswith("//"):
            u = "https:" + u
        if u in seen:
            continue
        seen.add(u)
        print(f"LOGO-URL {u}")
        try:
            data, kind = fetch(u)
            if u.lower().split("?")[0].endswith(".svg") or "svg" in kind:
                path = os.path.join(out, f"logo-{len(seen)}.svg")
                open(path, "wb").write(data)
                print(f"SAVED {path} (svg)")
            else:
                finish(Image.open(io.BytesIO(data)), os.path.join(out, f"logo-{len(seen)}.png"), 1200)
        except Exception as e:
            print(f"FAILED {u}: {e}")


if __name__ == "__main__":
    folder, site, out, names = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    logos(site, out)
    products(folder, [n for n in names.split("|") if n], out)
