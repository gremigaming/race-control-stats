"""The stream schedule picture for the Stream Schedule channel: one card per day
(Monday to Sunday), streams in colour, offline days muted, today outlined.

Needs Pillow. Uses the fonts in fonts/ (Anton for the big letters, Montserrat for
the small ones) and falls back to DejaVu when they are missing.
"""
import datetime
import io
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Bump when the picture is drawn differently, so the posted schedule is redrawn
LAYOUT = 1

W, H = 1080, 1440
S = 2  # drawn twice as large and shrunk, for smooth edges

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
TOP, BOTTOM = (74, 10, 34), (12, 38, 54)            # background, like GreMi's own
OFFLINE = (128, 52, 88)
STREAM_FROM, STREAM_TO = (214, 32, 64), (118, 46, 196)  # race red to Twitch purple
TWITCH = (145, 70, 255)
WHITE = (255, 255, 255)
MUTED = (170, 150, 165)


def _font(name, size, weight=None):
    try:
        f = ImageFont.truetype(os.path.join(FONT_DIR, name), size * S)
        if weight:
            try:
                f.set_variation_by_axes([weight])
            except Exception:
                pass
        return f
    except OSError:
        for fallback in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(fallback, int(size * S * 0.8))
            except OSError:
                continue
        return ImageFont.load_default()


def big(size):
    return _font("Anton-Regular.ttf", size)


def small(size, weight=800):
    return _font("Montserrat.ttf", size, weight)


def clean(text):
    """Title text the fonts can draw: the fancy bar Twitch titles use becomes a
    plain one, emoji and other symbols are dropped."""
    text = text.replace("∣", "|").replace("│", "|").replace("｜", "|")
    text = "".join(ch for ch in text if ord(ch) < 0x2000 or ch in "–—’")
    return " ".join(text.split()).upper()


def _width(draw, text, font):
    l, _, r, _ = draw.textbbox((0, 0), text, font=font)
    return r - l


def _wrap(draw, text, font, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if line and _width(draw, test, font) > max_w:
            lines.append(line)
            line = w
        else:
            line = test
    if line:
        lines.append(line)
    return lines


def fit(draw, text, max_w, max_h, sizes, max_lines=2):
    """(font, lines) for the biggest Anton size that fits the box."""
    for size in sizes:
        f = big(size)
        lines = _wrap(draw, text, f, max_w)
        line_h = f.size * 1.12
        if (len(lines) <= max_lines and line_h * len(lines) <= max_h
                and all(_width(draw, ln, f) <= max_w for ln in lines)):
            return f, lines
    f = big(sizes[-1])
    lines = _wrap(draw, text, f, max_w)[:max_lines]
    while lines and _width(draw, lines[-1] + "...", f) > max_w and " " in lines[-1]:
        lines[-1] = lines[-1].rsplit(" ", 1)[0]
    if len(_wrap(draw, text, f, max_w)) > max_lines:
        lines[-1] += "..."
    return f, lines


def _text_center(draw, cx, cy, lines, font, fill, line_h=None):
    line_h = line_h or font.size * 1.12
    top = cy - line_h * len(lines) / 2
    for i, ln in enumerate(lines):
        draw.text((cx, top + line_h * (i + 0.5)), ln, font=font, fill=fill, anchor="mm")


def _gradient(size, a, b, horizontal=False):
    w, h = size
    grad = Image.new("RGB", (256, 1) if horizontal else (1, 256))
    for i in range(256):
        t = i / 255
        c = tuple(round(a[k] + (b[k] - a[k]) * t) for k in range(3))
        grad.putpixel((i, 0) if horizontal else (0, i), c)
    return grad.resize((w, h), Image.BICUBIC)


def background():
    img = _gradient((W * S, H * S), TOP, BOTTOM).convert("RGBA")
    deco = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(deco)
    # a fading chequered flag in the top right corner
    sq = 34 * S
    for row in range(9):
        for col in range(12):
            if (row + col) % 2:
                continue
            x = W * S - (col + 1) * sq
            y = row * sq
            fade = max(0, 1 - (col / 12 + row / 9) / 1.1)
            d.rectangle((x, y, x + sq, y + sq), fill=(255, 255, 255, round(26 * fade)))
    # diagonal speed streaks
    for i, (x, width, alpha) in enumerate([(-200, 70, 10), (-40, 18, 14), (60, 8, 18),
                                           (720, 40, 7), (880, 10, 11)]):
        x *= S
        d.polygon([(x, H * S), (x + width * S, H * S),
                   (x + width * S + 900 * S, 0), (x + 900 * S, 0)],
                  fill=(255, 255, 255, alpha))
    img.alpha_composite(deco)
    return img


def _card(size, kind, radius):
    """A day card as an RGBA image: 'stream', 'offline' or 'cancelled'."""
    w, h = size
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
    if kind == "stream":
        fill = _gradient(size, STREAM_FROM, STREAM_TO, horizontal=True).convert("RGBA")
        over = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        for x in range(-h, w, 46 * S):  # racing stripes
            d.polygon([(x, h), (x + 14 * S, h), (x + 14 * S + h, 0), (x + h, 0)],
                      fill=(255, 255, 255, 14))
        d.rectangle((0, 0, w, h // 2), fill=(255, 255, 255, 10))  # soft top shine
        fill.alpha_composite(over)
        fill.putalpha(mask)
        return fill
    # muted cards fade out to the right, like GreMi's own schedule
    color = (90, 70, 80) if kind == "cancelled" else OFFLINE
    fade = _gradient(size, (150, 150, 150), (60, 60, 60), horizontal=True).convert("L")
    alpha = Image.composite(fade, Image.new("L", size, 0), mask)
    card = Image.new("RGBA", size, color + (0,))
    card.putalpha(alpha)
    return card


def _shadow(size, radius, blur=14):
    w, h = size
    pad = blur * 3
    sh = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad + 6 * S, pad + w, pad + h + 6 * S),
                                         radius, fill=(0, 0, 0, 120))
    return sh.filter(ImageFilter.GaussianBlur(blur)), pad


def _pill(draw, right, top, text, font):
    tw = _width(draw, text, font)
    padx, h = 18 * S, 40 * S
    box = (right - tw - padx * 2, top, right, top + h)
    draw.rounded_rectangle(box, 12 * S, outline=WHITE, width=3 * S)
    draw.text(((box[0] + box[2]) / 2, top + h / 2), text, font=font, fill=WHITE, anchor="mm")
    return box


def render(days, today, tz_label, channel_name, week_label):
    """PNG bytes of the schedule.

    days: seven (date, [stream, ...]) pairs, Monday first. A stream is a dict with
    "time" ("19:00"), "title", "category" (may be empty) and "cancelled" (bool).
    A day can also be marked with the dict {"vacation": True} as its only entry.
    """
    img = background()
    d = ImageDraw.Draw(img)

    # header
    head = big(128)
    d.text((56 * S, 30 * S), "STREAM", font=head, fill=WHITE)
    d.text((176 * S, 158 * S), "SCHEDULE", font=head, fill=WHITE)
    pill = small(22, 800)
    right = (W - 56) * S
    _pill(d, right, 44 * S, f"/{channel_name}", pill)
    _pill(d, right, 98 * S, tz_label, pill)
    d.text((right, 214 * S), week_label.upper(), font=small(30, 800),
           fill=(255, 255, 255, 215), anchor="rt")
    d.text((right, 254 * S), "LIVE ON TWITCH", font=small(20, 700),
           fill=(205, 180, 255, 200), anchor="rt")

    # day cards
    top, bottom, gap = 330, H - 96, 16
    row_h = (bottom - top - gap * 6) / 7
    x0, x1 = 56 * S, (W - 56) * S
    radius = 26 * S
    for i, (day, streams) in enumerate(days):
        y0 = round((top + i * (row_h + gap)) * S)
        y1 = round(y0 + row_h * S)
        size = (x1 - x0, y1 - y0)
        vacation = any(s.get("vacation") for s in streams)
        streams = [s for s in streams if not s.get("vacation")]
        live = [s for s in streams if not s.get("cancelled")]
        kind = "stream" if live else ("cancelled" if streams else "offline")
        past = day < today

        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        if kind == "stream":
            sh, pad = _shadow(size, radius)
            layer.alpha_composite(sh, (x0 - pad, y0 - pad))
        layer.alpha_composite(_card(size, kind, radius), (x0, y0))
        ld = ImageDraw.Draw(layer)
        if day == today:
            ld.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius, outline=WHITE, width=4 * S)
        weekday = day.strftime("%a").upper()
        left_cx = x0 + 110 * S
        text_x0, text_x1 = x0 + 220 * S, x1 - 40 * S
        cy = (y0 + y1) / 2

        if kind == "stream" or kind == "cancelled":
            shown = live or streams
            n = len(shown)
            sub_h = (y1 - y0) / n
            if n == 1:
                ld.text((left_cx, cy - 4 * S), shown[0]["time"], font=small(30, 800),
                        fill=WHITE, anchor="md")
                ld.text((left_cx, cy + 4 * S), weekday, font=small(26, 800),
                        fill=(255, 255, 255, 220), anchor="ma")
            else:
                ld.text((left_cx, cy), weekday, font=small(26, 800),
                        fill=(255, 255, 255, 220), anchor="mm")
            for k, s in enumerate(shown):
                sy0 = y0 + sub_h * k
                scy = sy0 + sub_h / 2
                title = clean(s["title"]) or "LIVE ON TWITCH"
                category = clean(s.get("category") or "")
                if s.get("cancelled"):
                    category = "CANCELLED"
                box_w = text_x1 - text_x0
                if n == 1:
                    cat_h = 30 * S if category else 0
                    f, lines = fit(ld, title, box_w, sub_h - cat_h - 22 * S,
                                   [50, 46, 42, 38, 34, 30, 26])
                    line_h = f.size * 1.12
                    block = line_h * len(lines) + cat_h
                    tcy = scy - block / 2 + line_h * len(lines) / 2
                    cx = (text_x0 + text_x1) / 2
                    _text_center(ld, cx + 2 * S, tcy + 3 * S, lines, f, (0, 0, 0, 90), line_h)
                    _text_center(ld, cx, tcy, lines, f, WHITE, line_h)
                    if category:
                        ld.text((cx, tcy + line_h * len(lines) / 2 + 6 * S), category,
                                font=small(19, 700), fill=(255, 255, 255, 200), anchor="ma")
                else:
                    f, lines = fit(ld, title, box_w - 110 * S, sub_h - 8 * S,
                                   [34, 30, 26, 22], max_lines=1)
                    ld.text((text_x0, scy), s["time"], font=small(22, 800),
                            fill=WHITE, anchor="lm")
                    ld.text((text_x0 + 100 * S, scy), lines[0] if lines else "", font=f,
                            fill=WHITE, anchor="lm")
            if kind == "cancelled":
                ld.line((text_x0, cy, text_x1, cy), fill=(255, 255, 255, 120), width=3 * S)
        else:
            ld.text((left_cx, cy), weekday, font=small(26, 800), fill=MUTED, anchor="mm")
            label = "VACATION" if vacation else "OFFLINE"
            ld.text(((text_x0 + text_x1) / 2, cy), label, font=big(50),
                    fill=(150, 135, 150), anchor="mm")

        if past:  # days already done fade back
            a = layer.getchannel("A").point(lambda v: v * 45 // 100)
            layer.putalpha(a)
        img.alpha_composite(layer)

    # footer
    fy = (H - 52) * S
    d.rounded_rectangle((56 * S, fy - 11 * S, 78 * S, fy + 11 * S), 5 * S, fill=TWITCH)
    d.text((92 * S, fy), f"twitch.tv/{channel_name.lower()}", font=small(24, 700),
           fill=(255, 255, 255, 220), anchor="lm")
    d.text(((W - 56) * S, fy), "UPDATES AUTOMATICALLY", font=small(18, 700),
           fill=(255, 255, 255, 120), anchor="rm")

    out = io.BytesIO()
    img.convert("RGB").resize((W, H), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


if __name__ == "__main__":
    # A sample picture, to look at the design: python3 schedule_image.py out.png
    import sys
    monday = datetime.date(2026, 10, 5)
    sample = {2: [{"time": "19:00", "title": "F1 26 LOBBIES ∣ RANDOM GRID",
                   "category": "F1 25", "cancelled": False}],
              4: [{"time": "19:00", "title": "F1 26 LOBBIES ∣ ONE SHOT QUALIFYING",
                   "category": "F1 25", "cancelled": False}],
              5: [{"time": "15:00", "title": "LMU daily races, chill stream",
                   "category": "Le Mans Ultimate", "cancelled": False},
                  {"time": "21:00", "title": "Late night hotlaps", "category": "",
                   "cancelled": False}],
              6: [{"time": "19:00", "title": "F1 26 LOBBIES ∣ RANDOM GRID",
                   "category": "F1 25", "cancelled": False}]}
    days = [(monday + datetime.timedelta(i), sample.get(i, [])) for i in range(7)]
    with open(sys.argv[1] if len(sys.argv) > 1 else "schedule.png", "wb") as fh:
        fh.write(render(days, monday + datetime.timedelta(2), "GMT+2", "GreMi_Gaming",
                        "5 - 11 Oct"))
