"""Text of the live boards in the F1 channels: the championship of this month
and the safety rating of every driver. The bot posts each board once and edits
it when the numbers change (bot/sr_roles.py). No Discord code here, so it can
be tested on its own."""
import datetime

from bot import links as L

CHAMPIONSHIP_URL = "https://gremigaming.github.io/race-stats/league/championship.json"
LEADERBOARD_URL = "https://gremigaming.github.io/race-stats/all/drivers"
CHAMPIONSHIP_TITLE = "F1 championship"
SAFETY_TITLE = "Safety rating"
EMBED_TEXT = 3800  # Discord allows 4096 per embed (with the intro) and 6000 per message
MESSAGE_TEXT = 5600
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def month_name(key):
    year, month = key.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def pages(lines, head="", more=" and {n} more on the site"):
    """Splits table lines into code blocks that fit embeds of one message.
    Lines that don't fit are left out with a note."""
    out, cur, total = [], [], 0
    for i, line in enumerate(lines):
        block = "\n".join([head] + cur + [line])
        if len(block) + 8 > EMBED_TEXT:
            out.append(cur)
            cur = []
        if total + len(line) + 1 + len(head) + 8 > MESSAGE_TEXT:
            if cur:
                out.append(cur)
            return ["```\n" + "\n".join([head] + p) + "\n```" for p in out if p], more.format(n=len(lines) - i)
        cur.append(line)
        total += len(line) + 1
    if cur:
        out.append(cur)
    return ["```\n" + "\n".join([head] + p) + "\n```" for p in out], ""


def championship(data, today=None):
    """(title, [descriptions], footer) for this month's standings, or the last
    month with races (as final standings) when nobody raced this month yet."""
    months = (data or {}).get("months") or {}
    today = today or datetime.date.today()
    now = f"{today.year}-{today.month:02d}"
    key = now if months.get(now) else max(months, default=None)
    if key is None:
        return CHAMPIONSHIP_TITLE, ["No races yet."], ""
    rows = months[key]
    final = key != now
    races = max((r["races"] for r in rows), default=0)
    lines = []
    for i, r in enumerate(rows):
        lines.append(f"{i + 1:>2} {r['points']:>4}  {r['wins']:>2} {r['podiums']:>2}  {r['races']:>2}  {r['name'][:24]}")
    blocks, extra = pages(lines, " #  Pts   W  P   R  Driver")
    intro = (f"Final standings of {month_name(key)}; nobody raced yet in {month_name(now)}.\n"
             if final else f"Standings of {month_name(key)}, live after every stream.\n")
    blocks[0] = intro + blocks[0]
    footer = ("Pts points (25-18-15-12-10-8-6-4-2-1) · W wins · P podiums · R races"
              + extra + " · new month, new championship")
    return f"{CHAMPIONSHIP_TITLE}: {month_name(key)}", blocks, footer


def safety(data, link_channel=None):
    """(title, [descriptions], footer) for the SR ranking, removed drivers left out."""
    drivers = [d for d in L.drivers_of(data).values() if not d.get("banned")]
    if not drivers:
        return SAFETY_TITLE, ["No ratings yet."], ""
    lines = []
    for i, d in enumerate(drivers):
        change = d.get("change", 0)
        sign = "+" if change > 0 else "-" if change < 0 else " "
        delta = f"{sign}{abs(change):.1f}" if change else "   -"
        star = "*" if d.get("provisional") else " "
        lines.append(f"{i + 1:>2} {L.rank_of(d['sr']):<2} {d['sr']:>5.1f}{star} {delta:>5}  {d['name'][:24]}")
    blocks, extra = pages(lines, " # Rk    SR   Last  Driver")
    blocks[0] = ("Everyone's safety rating, live after every stream. Clean racing raises it; "
                 "contacts, penalties and retirements lower it."
                 + (f" Link your name in <#{link_channel}> to get your SR role." if link_channel else "")
                 + "\n" + blocks[0])
    footer = ("Last = change last stream · * provisional until 3 races" + extra)
    return SAFETY_TITLE, blocks, footer
