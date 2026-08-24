#!/usr/bin/env python3
"""Render a 1080x1920 daily quote story card.

Picks the next unused quote from quotes/quotes.json (tracked in state/used.json),
renders it on a themed gradient card, writes queue/pending/<date>.jpg and prints
a JSON line with the metadata the workflows need.

Usage:
  python3 scripts/gen_story.py                 # next unused quote, today's date
  python3 scripts/gen_story.py --id 7          # specific quote
  python3 scripts/gen_story.py --date 2026-08-24
"""
import argparse
import json
import os
import random
import sys
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1080, 1920

# (top, mid, bottom, accent, text) — one theme per day, rotated by quote id
THEMES = [
    ((10, 6, 12),  (72, 10, 28),  (6, 4, 8),   (200, 60, 84),   (245, 236, 238)),  # wine
    ((6, 8, 14),   (14, 26, 58),  (4, 6, 10),  (96, 148, 220),  (232, 238, 248)),  # midnight blue
    ((8, 10, 8),   (16, 44, 28),  (4, 8, 6),   (96, 190, 130),  (232, 244, 236)),  # deep green
    ((12, 10, 6),  (64, 44, 12),  (8, 6, 4),   (222, 168, 62),  (248, 242, 230)),  # gold
    ((10, 8, 12),  (44, 20, 60),  (6, 4, 10),  (168, 110, 220), (240, 234, 246)),  # violet
    ((10, 10, 10), (36, 36, 40),  (5, 5, 6),   (200, 200, 205), (244, 244, 246)),  # mono
]


def load_font(size, italic=False):
    candidates = [
        os.path.join(ROOT, "fonts", "PlayfairDisplay-Italic.ttf" if italic else "PlayfairDisplay-Bold.ttf"),
        "/System/Library/Fonts/Supplemental/Georgia Italic.ttf" if italic else "/System/Library/Fonts/Supplemental/Didot.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf" if italic else "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap(draw, text, font, max_width):
    lines, line = [], []
    for word in text.split():
        trial = " ".join(line + [word])
        if draw.textlength(trial, font=font) <= max_width or not line:
            line.append(word)
        else:
            lines.append(" ".join(line))
            line = [word]
    lines.append(" ".join(line))
    return lines


def render(quote, author, theme_idx, out_path, handle, series_tag):
    top, mid, bot, accent, text_col = THEMES[theme_idx % len(THEMES)]
    img = Image.new("RGB", (W, H), bot)
    d = ImageDraw.Draw(img)

    for y in range(H):
        t = y / H
        if t < 0.55:
            k = t / 0.55
            c = tuple(int(top[i] + (mid[i] - top[i]) * k) for i in range(3))
        else:
            k = (t - 0.55) / 0.45
            c = tuple(int(mid[i] + (bot[i] - mid[i]) * k) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)

    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W // 2 - 420, H // 2 - 380, W // 2 + 420, H // 2 + 260],
               fill=tuple(min(255, int(c * 0.6)) for c in accent))
    glow = glow.filter(ImageFilter.GaussianBlur(200))
    img = Image.blend(img, glow, 0.30)
    d = ImageDraw.Draw(img)

    px = img.load()
    rnd = random.Random(theme_idx)
    for _ in range(50000):
        x, y = rnd.randrange(W), rnd.randrange(H)
        r, g, b = px[x, y]
        n = rnd.randint(-9, 9)
        px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))

    # series tag, top
    tag_f = load_font(40)
    tw = d.textlength(series_tag, font=tag_f)
    d.text(((W - tw) / 2, 210), series_tag, font=tag_f, fill=accent)

    # opening quote mark
    mark_f = load_font(260)
    d.text((W // 2 - 70, H // 2 - 640), "“", font=mark_f, fill=accent)

    # quote body: shrink until it fits
    size = 118
    while size > 56:
        body_f = load_font(size)
        lines = wrap(d, quote, body_f, W - 240)
        line_h = int(size * 1.32)
        if len(lines) * line_h <= 760:
            break
        size -= 8
    total_h = len(lines) * line_h
    y = H // 2 - 60 - total_h // 2
    for ln in lines:
        lw = d.textlength(ln, font=body_f)
        d.text(((W - lw) / 2, y), ln, font=body_f, fill=text_col)
        y += line_h

    # attribution
    attr_f = load_font(58, italic=True)
    attr = f"— {author}"
    aw = d.textlength(attr, font=attr_f)
    d.text(((W - aw) / 2, y + 70), attr, font=attr_f, fill=accent)

    # divider + handle watermark, bottom
    d.line([(W // 2 - 120, H - 320), (W // 2 + 120, H - 320)], fill=accent, width=2)
    hf = load_font(42)
    ht = f"@{handle}"
    hw = d.textlength(ht, font=hf)
    d.text(((W - hw) / 2, H - 280), ht, font=hf, fill=text_col)

    img.save(out_path, quality=92)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, default=None)
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()

    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    quotes = json.load(open(os.path.join(ROOT, "quotes", "quotes.json")))
    used_path = os.path.join(ROOT, "state", "used.json")
    used = json.load(open(used_path)) if os.path.exists(used_path) else []

    if args.id is not None:
        q = next((x for x in quotes if x["id"] == args.id), None)
    else:
        q = next((x for x in quotes if x["id"] not in used), None)
    if q is None:
        print("ERROR: no unused quotes left — refill quotes/quotes.json", file=sys.stderr)
        sys.exit(1)

    out = os.path.join(ROOT, "queue", "pending", f"{args.date}.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    render(q["quote"], q["author"], q["id"], out, cfg["handle"], cfg["series_tag"])

    if q["id"] not in used:
        used.append(q["id"])
        json.dump(used, open(used_path, "w"))

    print(json.dumps({
        "file": os.path.relpath(out, ROOT),
        "quote": q["quote"],
        "author": q["author"],
        "id": q["id"],
        "date": args.date,
    }))


if __name__ == "__main__":
    main()
