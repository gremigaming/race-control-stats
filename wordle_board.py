"""Wordle leaderboard in the WORDLE channel, run by Race Control (bot/wordle.py).

The Wordle app posts "yesterday's results" every morning where people play
(mostly GENERAL). Each run reads its new posts since last time, saves the
results (on the bot's server; wordle.json here was the starting copy), gives
every player the Wordle role (that role is what lets people see the WORDLE
channel) and redraws the leaderboard picture.

Points per game: 1/6 = 6, 2/6 = 5 ... 6/6 = 1, X = 0, and the day's crown
(best of the group) +1. One message per month: edited while the month runs,
a new month gets a new message so last month's final standings stay.
"""
import collections
import datetime
import hashlib
import io
import json
import os
import re
import urllib.parse

WORDLE_BOT_ID = "1211781489931452447"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wordle.json")
WORDLE_CHANNEL_ID = os.environ.get("WORDLE_CHANNEL_ID", "")
WORDLE_ROLE_ID = os.environ.get("WORDLE_ROLE_ID", "")
# Where the Wordle app posts (GENERAL, plus the WORDLE channel itself)
SOURCE_CHANNELS = [c for c in os.environ.get(
    "WORDLE_SOURCE_CHANNELS", "1079917340298321942").split(",") if c] + (
    [WORDLE_CHANNEL_ID] if WORDLE_CHANNEL_ID else [])
FILE_PREFIX = "wordle-"
# Bump when the picture is drawn differently, so the live board is redrawn
LAYOUT = 1

SUMMARY = re.compile(r"on an? (\d+) day streak.*results:", re.S)
LINE = re.compile(r"^(👑 )?([1-6X])/6: (.*)$")


# ---------- reading the Wordle posts ----------

def parse_summary(content):
    """[(guesses, crown, [user ids], [plain @names])] from one daily summary.
    X counts as 7 guesses."""
    out = []
    for line in content.split("\n")[1:]:
        m = LINE.match(line.strip())
        if not m:
            continue
        rest = m.group(3)
        ids = re.findall(r"<@!?(\d+)>", rest)
        # people the app couldn't mention show up as "@Server Nick"
        plain = [p.strip().replace("\\", "") for p in
                 re.findall(r"@([^<@]+)", re.sub(r"<@!?\d+>", "", rest)) if p.strip()]
        out.append((7 if m.group(2) == "X" else int(m.group(2)), bool(m.group(1)), ids, plain))
    return out


def summary_day(msg, chan_state):
    """The day a summary is about. The streak number counts days, which is
    steadier than the time of the post (that drifts by hours)."""
    streak = int(SUMMARY.search(msg["content"]).group(1))
    posted = datetime.datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
    guess = (posted - datetime.timedelta(days=1)).date()
    last_day, last_streak = chan_state.get("last_day"), chan_state.get("last_streak")
    if last_day and last_streak and streak > last_streak:
        day = datetime.date.fromisoformat(last_day) + datetime.timedelta(days=streak - last_streak)
    elif last_day:
        day = max(guess, datetime.date.fromisoformat(last_day) + datetime.timedelta(days=1))
    else:
        day = guess
    chan_state["last_day"], chan_state["last_streak"] = str(day), streak
    return str(day)


def new_messages(stats, channel, after):
    """Messages after the given id, oldest first."""
    out = []
    while True:
        page = stats.discord("GET", f"/channels/{channel}/messages?limit=100&after={after}")
        if not page:
            return out
        page.sort(key=lambda m: int(m["id"]))
        out += page
        after = page[-1]["id"]
        if len(page) < 100:
            return out


def member(stats, user_id):
    try:
        return stats.discord("GET", f"/guilds/{stats.GUILD_ID}/members/{user_id}")
    except RuntimeError as e:
        if " 404 " in str(e):
            return None
        raise


def find_by_name(stats, name):
    """The one member whose server nick or name is exactly this, else None."""
    found = stats.discord("GET", f"/guilds/{stats.GUILD_ID}/members/search?limit=10&query="
                          + urllib.parse.quote(name))
    hits = [m["user"]["id"] for m in found if name.lower() in {
        (m.get("nick") or "").lower(), (m["user"].get("global_name") or "").lower(),
        m["user"]["username"].lower()}]
    return hits[0] if len(hits) == 1 else None


def display_name(m):
    return m.get("nick") or m["user"].get("global_name") or m["user"]["username"]


def read_new_results(stats, state):
    """Adds new summaries to state; returns the ids of everyone seen playing."""
    players = set()
    for channel in SOURCE_CHANNELS:
        chan = state["channels"].setdefault(channel, {})
        if not chan.get("after"):
            # a new channel: start from now, older posts there aren't ours to count
            latest = stats.discord("GET", f"/channels/{channel}/messages?limit=1")
            chan["after"] = latest[0]["id"] if latest else "0"
            continue
        for msg in new_messages(stats, channel, chan["after"]):
            if msg["author"]["id"] != WORDLE_BOT_ID:
                continue
            # only moves on Wordle posts, so wordle.json (committed) doesn't change on every chat line
            chan["after"] = msg["id"]
            user = (msg.get("interaction_metadata") or {}).get("user") or {}
            if user.get("id"):
                players.add(user["id"])  # "X was playing": give the role right away
            if not SUMMARY.search(msg["content"]):
                continue
            day = state["days"].setdefault(summary_day(msg, chan), {})
            for guesses, crown, ids, plain in parse_summary(msg["content"]):
                for name in plain:
                    found = state["aliases"].get(name.lower()) or find_by_name(stats, name)
                    if found:
                        state["aliases"][name.lower()] = found
                        ids.append(found)
                    else:
                        print(f"wordle: no member called {name!r}")
                for i in ids:
                    old = day.get(i)
                    if not old or guesses < old[0]:
                        day[i] = [guesses, int(crown)]
                    players.add(i)
    return players


# ---------- standings ----------

def standings(state, days):
    """Rows for everyone still in the server who played on these days, best first."""
    games = collections.defaultdict(list)
    for d in days:
        for i, (g, crown) in state["days"].get(d, {}).items():
            games[i].append((d, g, crown))
    last = max(state["days"]) if state["days"] else None
    rows = []
    for i, gs in games.items():
        name = state["names"].get(i)
        if not name:
            continue  # left the server
        solved = [g for _, g, _ in gs if g < 7]
        played = {d for d, _, _ in gs}
        streak, d = 0, datetime.date.fromisoformat(last)
        while str(d) in state["days"] and i in state["days"][str(d)]:
            streak += 1
            d -= datetime.timedelta(days=1)
        rows.append({
            "id": i, "name": name,
            "points": sum(7 - g + c for _, g, c in gs),
            "played": len(played),
            "solved": len(solved) / len(gs),
            "avg": sum(solved) / len(solved) if solved else None,
            "crowns": sum(c for _, _, c in gs),
            "streak": streak,
            "dist": [sum(1 for _, g, _ in gs if g == k) for k in range(1, 8)],
        })
    rows.sort(key=lambda r: (-r["points"], r["avg"] or 9, r["name"].lower()))
    return rows


# ---------- picture ----------

BG, PANEL, LINE_C = (11, 11, 13), (22, 22, 24), (37, 37, 41)
WHITE, GREY, DIM = (240, 240, 242), (141, 141, 147), (110, 110, 116)
GREEN, YELLOW, DARK, RED = (83, 141, 78), (181, 159, 59), (58, 58, 60), (122, 52, 52)
GOLD, ORANGE = (242, 201, 76), (240, 163, 94)
MEDALS = [(242, 201, 76), (198, 204, 212), (205, 127, 50)]
FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
S = 2  # drawn at twice the size, sharp on phones


def _font(name, size):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(FONTS, name), size * S)


def _clean(name, limit=22):
    """Names without emoji and other symbols the font can't draw."""
    name = "".join(ch for ch in name if ord(ch) < 0x2000 or 0x2070 <= ord(ch) < 0x2190).strip()
    return name if len(name) <= limit else name[:limit - 1] + "…"


def _crown(draw, x, y, size, color):
    s = size * S
    x, y = x * S, y * S
    draw.polygon([(x, y + s), (x, y + s * .3), (x + s * .27, y + s * .62), (x + s * .5, y),
                  (x + s * .73, y + s * .62), (x + s, y + s * .3), (x + s, y + s)], fill=color)


def _flame(draw, x, y, size, color):
    s = size * S
    x, y = x * S, y * S
    draw.ellipse([x, y + s * .35, x + s * .8, y + s], fill=color)
    draw.polygon([(x + s * .02, y + s * .62), (x + s * .45, y), (x + s * .78, y + s * .62)], fill=color)


COLS = [  # (title, right edge x)
    ("Points", 470), ("Played", 560), ("Solved", 650), ("Avg", 725),
    ("Crowns", 815), ("Streak", 905)]
WIDTH = 1120
ROW = 44


def _table(draw, top, title, note, rows):
    f_h2, f_note = _font("Inter-Bold.otf", 24), _font("DejaVuSansMono.ttf", 13)
    f_head, f_name = _font("Inter-Regular.otf", 16), _font("Inter-SemiBold.otf", 18)
    f_num, f_bold = _font("DejaVuSansMono.ttf", 17), _font("Inter-Bold.otf", 18)
    left, right = 40, WIDTH - 40
    height = 120 + ROW * max(len(rows), 1) + 16
    draw.rounded_rectangle([left * S, top * S, right * S, (top + height) * S], 20 * S, fill=PANEL)
    x0 = left + 26
    draw.text((x0 * S, (top + 22) * S), title, font=f_h2, fill=WHITE)
    draw.text((x0 * S, (top + 58) * S), note, font=f_note, fill=GREY)
    y = top + 92
    draw.text(((x0 + 8) * S, y * S), "#", font=f_head, fill=GREY)
    draw.text(((x0 + 44) * S, y * S), "Player", font=f_head, fill=GREY)
    for name, edge in COLS:
        draw.text((edge * S, y * S), name, font=f_head, fill=GREY, anchor="ra")
    draw.text((1000 * S, y * S), "Guesses 1-6 · X", font=f_head, fill=GREY, anchor="ma")
    y += 30
    draw.line([x0 * S, y * S, (right - 26) * S, y * S], fill=LINE_C, width=S)
    if not rows:
        draw.text(((x0 + 44) * S, (y + 12) * S), "No games yet this month", font=f_name, fill=GREY)
    for n, r in enumerate(rows, 1):
        mid = y + ROW / 2
        if n <= 3:
            cx = x0 + 14
            draw.ellipse([(cx - 13) * S, (mid - 13) * S, (cx + 13) * S, (mid + 13) * S], fill=MEDALS[n - 1])
            draw.text((cx * S, mid * S), str(n), font=f_bold, fill=BG, anchor="mm")
        else:
            draw.text(((x0 + 14) * S, mid * S), str(n), font=f_num, fill=DIM, anchor="mm")
        draw.text(((x0 + 44) * S, mid * S), _clean(r["name"]), font=f_name, fill=WHITE, anchor="lm")
        avg = f"{r['avg']:.2f}" if r["avg"] else "-"
        values = [(str(r["points"]), f_bold, WHITE), (str(r["played"]), f_num, WHITE),
                  (f"{r['solved'] * 100:.0f}%", f_num, WHITE), (avg, f_num, WHITE),
                  (str(r["crowns"]), f_num, GOLD),
                  (str(r["streak"]) if r["streak"] else "-", f_num, ORANGE if r["streak"] >= 3 else GREY)]
        for (text, font, color), (_, edge) in zip(values, COLS):
            draw.text((edge * S, mid * S), text, font=font, fill=color, anchor="rm")
        if r["crowns"]:
            w = draw.textlength(str(r["crowns"]), font=f_num) / S
            _crown(draw, 815 - w - 22, mid - 8, 16, GOLD)
        if r["streak"] >= 3:
            w = draw.textlength(str(r["streak"]), font=f_num) / S
            _flame(draw, 905 - w - 20, mid - 9, 16, ORANGE)
        peak = max(r["dist"]) or 1
        for k, count in enumerate(r["dist"]):
            h = 4 + 26 * count / peak
            bx = 958 + k * 13
            draw.rounded_rectangle([bx * S, (mid + 15 - h) * S, (bx + 10) * S, (mid + 15) * S],
                                   2 * S, fill=RED if k == 6 else GREEN)
        y += ROW
        if n < len(rows):
            draw.line([x0 * S, y * S, (right - 26) * S, y * S], fill=LINE_C, width=S)
    return top + height


def picture(state, month):
    """PNG bytes of this month's board with the all-time top 10 under it."""
    from PIL import Image, ImageDraw
    month_days = [d for d in state["days"] if d.startswith(month)]
    month_rows = standings(state, month_days)[:15]
    all_rows = standings(state, list(state["days"]))[:10]
    height = 140 + (136 + ROW * max(len(month_rows), 1)) + 24 + (136 + ROW * len(all_rows)) + 40
    img = Image.new("RGB", (WIDTH * S, height * S), BG)
    draw = ImageDraw.Draw(img)
    x = 40
    for letter, color in zip("WORDLE", [GREEN, YELLOW, GREEN, DARK, GREEN, GREEN]):
        draw.rounded_rectangle([x * S, 40 * S, (x + 32) * S, 72 * S], 4 * S, fill=color)
        draw.text(((x + 16) * S, 56 * S), letter, font=_font("Inter-Bold.otf", 19), fill=WHITE, anchor="mm")
        x += 36
    draw.text(((x + 12) * S, 56 * S), "GreMi Gang Wordle", font=_font("Inter-Bold.otf", 32),
              fill=WHITE, anchor="lm")
    first = min(state["days"]) if state["days"] else month + "-01"
    sub = (f"{len(month_days)} days this month · {len(month_rows)} players · "
           f"{len(state['days'])} days since {_nice(first)}")
    draw.text((40 * S, 92 * S), sub, font=_font("Inter-Regular.otf", 18), fill=GREY)
    bottom = _table(draw, 136, _month_title(month),
                    "Points: 1/6 = 6 · 2/6 = 5 · ... · 6/6 = 1 · X = 0 · crown (best of the day) +1",
                    month_rows)
    _table(draw, bottom + 24, "All time", "Top 10 since the start · only people still in the server",
           all_rows)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _month_title(month):
    y, m = (int(x) for x in month.split("-"))
    return datetime.date(y, m, 1).strftime("%B %Y")


def _nice(day):
    d = datetime.date.fromisoformat(day)
    return f"{d.day} {d.strftime('%B %Y')}"


# ---------- Discord ----------

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    for key in ("channels", "days", "names", "aliases"):
        state.setdefault(key, {})
    return state


def save_state(state):
    state["days"] = dict(sorted(state["days"].items()))
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def sync_players(stats, state, ids):
    """Fresh server names for these players, and the Wordle role for those without it."""
    for i in sorted(ids):
        m = member(stats, i)
        if m is None:
            state["names"][i] = None
            continue
        state["names"][i] = display_name(m)
        if WORDLE_ROLE_ID and WORDLE_ROLE_ID not in m.get("roles", []):
            stats.discord("PUT", f"/guilds/{stats.GUILD_ID}/members/{i}/roles/{WORDLE_ROLE_ID}")
            print(f"wordle: role given to {i}")


def update(stats):
    if not WORDLE_CHANNEL_ID:
        return
    import socials_board
    state = load_state()
    players = read_new_results(stats, state)
    if players:
        sync_players(stats, state, players)
    save_state(state)
    if not state["days"]:
        return
    month = max(state["days"])[:7]
    digest = hashlib.sha256(json.dumps(
        [LAYOUT, standings(state, [d for d in state["days"] if d.startswith(month)]),
         standings(state, list(state["days"]))[:10]], sort_keys=True).encode()).hexdigest()[:12]
    name = f"{FILE_PREFIX}{month}-{digest}.png"
    me = stats.discord("GET", "/users/@me")["id"]
    posts = [m for m in stats.discord("GET", f"/channels/{WORDLE_CHANNEL_ID}/messages?limit=50")
             if m["author"]["id"] == me and any(
                 a.get("filename", "").startswith(f"{FILE_PREFIX}{month}-") for a in m.get("attachments", []))]
    if posts and any(a["filename"] == name for a in posts[0]["attachments"]):
        print("unchanged: wordle board")
        return
    png = picture(state, month)
    message = {
        "content": f"## 🟩 GreMi Gang Wordle · {_month_title(month)}\n"
                   "-# Updates every morning when the Wordle app posts yesterday's results · "
                   "play with /wordle in any chat",
        "attachments": [{"id": 0, "filename": name}],
        "allowed_mentions": {"parse": []},
    }
    if posts:
        socials_board.send(stats, "PATCH", f"/channels/{WORDLE_CHANNEL_ID}/messages/{posts[0]['id']}",
                           message, [(name, png)])
        print("updated:   wordle board")
        return
    msg = socials_board.send(stats, "POST", f"/channels/{WORDLE_CHANNEL_ID}/messages", message, [(name, png)])
    print(f"posted:    wordle board {msg.get('id')}")
