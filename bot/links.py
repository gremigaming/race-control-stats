"""Links Discord members to their driver name on the race stats site, so the
bot can give them the safety rating (SR) role of their rank.

A member links themself with /link, or the bot links them when their Discord
name matches a driver name exactly or almost (auto). Staff can fix links on
the staff page. A driver name belongs to one member at a time.
Stored on the server only (LINKS file), never in this public repo.
No Discord code here, so it can be tested on its own.
"""
import contextlib
import difflib
import fcntl
import json
import os
import pathlib
import re
import time

from bot.members import squash
from bot.replies import plain

PATH = pathlib.Path(os.environ.get("RACE_CONTROL_LINKS", "/var/lib/race-control/links.json"))
SAFETY_URL = "https://gremigaming.github.io/race-stats/league/safety.json"
SITE_URL = "https://gremigaming.github.io/race-stats/all/safety"
HIDDEN = re.compile(r" #\d+$")  # AI and hidden names, e.g. "Ferrari '26 #28"

# Same ranks and colours as the site (src/league/safety.ts)
RANKS = [("S+", 100, 0xFCD34D), ("S", 90, 0xC084FC), ("A", 70, 0x4ADE80), ("B", 50, 0xFACC15),
         ("C", 40, 0xFBBF24), ("D", 30, 0xFB923C), ("E", 20, 0xF87171), ("F", float("-inf"), 0xDC2626)]
ROLE_PREFIX = "SR "
NEAR = 0.85  # how alike two names must be to link them by themselves


def rank_of(sr):
    return next(r for r, low, _ in RANKS if sr >= low)


def role_name(rank):
    return ROLE_PREFIX + rank


def drivers_of(safety):
    """{driver name: driver entry} for everyone with a rating, hidden names left out."""
    return {d["name"]: d for d in (safety or {}).get("drivers", []) if not HIDDEN.search(d["name"])}


def personal_url(name):
    from urllib.parse import quote
    return f"{SITE_URL}?tab=personal&driver={quote(re.sub(r'\s+', ' ', name).strip().lower())}"


# Real F1 driver names many players use; too common to link by themselves
FAMOUS = {"verstappen", "hamilton", "leclerc", "norris", "piastri", "russell", "sainz", "alonso",
          "perez", "gasly", "ocon", "albon", "stroll", "tsunoda", "lawson", "hulkenberg", "bottas",
          "senna", "schumacher", "vettel", "raikkonen", "button", "rosberg", "max", "lewis", "charles",
          "lando", "oscar", "george", "carlos", "fernando", "yuki", "nico", "kimi", "ayrton", "michael"}


def tag(name):
    """The clan tag in front, like "QDR" in "[QDR] Shw1ks", or ""."""
    m = re.match(r"^\s*\[([^\]]*)\]", name or "")
    return plain(m.group(1)) if m else ""


def key(name):
    """'[QDR] Shw1ks' and 'TTV/GreMi_Gaming' to 'shw1ks' and 'gremigaming'; digits stay."""
    k = re.sub(r"[^a-z0-9]", "", plain(re.sub(r"^\s*\[[^\]]*\]\s*", "", name)))
    return k.removeprefix("ttv")


def full(name):
    return re.sub(r"[^a-z0-9]", "", plain(name)).removeprefix("ttv")


def _score(names, driver):
    dk, dl, dt = key(driver), squash(driver), tag(driver)
    if len(dk) < 3 or re.sub(r"\d", "", dk) in FAMOUS:
        return 0.0
    best = 0.0
    for n in names:
        mk, ml, mt = key(n), squash(n), tag(n)
        if len(mk) < 3 or (mt and dt and mt != dt):
            continue  # different clan tags: different people
        if mk == dk or full(n) == full(driver):
            return 1.0  # also "[QDR] Shw1ks" and "QDR_SHW1KS"
        short, long_ = sorted((mk, dk), key=len)
        if len(short) >= 5 and short in long_:
            if len(short) / len(long_) >= 0.75:
                best = max(best, 0.95)  # "Jakubm18" in "Jakubm18_F1"
            continue
        if re.sub(r"\D", "", mk) and re.sub(r"\D", "", dk) and re.sub(r"\D", "", mk) != re.sub(r"\D", "", dk):
            continue  # "ZenoK_22" and "zeno_k25" are likely different players
        if len(ml) >= 5 and ml == dl:
            best = max(best, 0.95)  # same apart from leet letters ("Shw1ks" and "Shwiks")
        elif min(len(mk), len(dk)) >= 5:
            r = difflib.SequenceMatcher(None, mk, dk).ratio()
            best = max(best, r if r >= 0.88 else 0.0)
    return best


def match(names, drivers):
    """The one driver whose name matches one of the member's names exactly or
    almost, or None when nothing (or more than one driver) matches well."""
    names = [n for n in names if n]
    scored = sorted(((_score(names, d), d) for d in drivers if not HIDDEN.search(d)), reverse=True)
    if not scored or scored[0][0] < NEAR:
        return None
    if len(scored) > 1 and scored[1][0] >= scored[0][0] - 0.03:
        return None  # two drivers look alike, let the member pick
    return scored[0][1]


class Links:
    def __init__(self, path=PATH):
        self.path = pathlib.Path(path)

    @contextlib.contextmanager
    def _locked(self):
        """The bot and the staff page both write this file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self.read()
            yield data
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
            tmp.replace(self.path)

    def read(self):
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def get(self, uid):
        return self.read().get(str(uid))

    def owner(self, driver, data=None):
        data = self.read() if data is None else data
        return next((int(u) for u, v in data.items() if v["driver"].lower() == driver.lower()), None)

    def link(self, uid, driver, how, by=None):
        """(True, message) or (False, why not). A self or staff link replaces an
        auto link; nobody can take a name another member linked themself."""
        with self._locked() as data:
            owner = self.owner(driver, data)
            if owner is not None and owner != int(uid):
                if how != "staff" and data[str(owner)]["how"] != "auto":
                    return False, f"{driver} is already linked to another member. Ask a mod if that's wrong."
                del data[str(owner)]
            data[str(uid)] = {"driver": driver, "how": how, "at": int(time.time()), **({"by": by} if by else {})}
        return True, f"Linked to {driver}."

    def unlink(self, uid):
        with self._locked() as data:
            return data.pop(str(uid), None) is not None

    def auto(self, members, drivers):
        """Links members whose name matches a driver, skipping anyone already
        linked, names already taken, and names two members both match.
        members: [(uid, [their names])]. Returns the new links [(uid, driver)]."""
        data = self.read()
        taken = {v["driver"].lower() for v in data.values()}
        free = [d for d in drivers if d.lower() not in taken]
        found = {}
        for uid, names in members:
            if str(uid) in data:
                continue
            d = match(names, free)
            if d:
                found.setdefault(d, []).append(uid)
        new = [(uids[0], d) for d, uids in found.items() if len(uids) == 1]
        if new:
            with self._locked() as data:
                for uid, d in new:
                    if str(uid) not in data and self.owner(d, data) is None:
                        data[str(uid)] = {"driver": d, "how": "auto", "at": int(time.time())}
        return new


def wanted_roles(links, drivers):
    """{uid: rank} for every linked member with a rating; None means no SR role
    (removed drivers, or a name without a rating)."""
    out = {}
    for uid, v in links.items():
        d = drivers.get(v["driver"])
        out[int(uid)] = rank_of(d["sr"]) if d and not d.get("banned") else None
    return out
