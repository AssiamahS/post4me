#!/usr/bin/env python3
"""Render one roadmap entry into a 1080x1920 reel — the Canva-AI step, minus Canva.

  subject        ──Wikipedia/Commons─▶ photo.jpg (CC-licensed, credit drawn on card)
  voiceover text ──edge-tts──▶ voice.mp3 + word timings
  word timings   ──PIL───────▶ one caption card per 3-4 word beat
  cards + voice  ──ffmpeg────▶ reel.mp4 (H.264/AAC, slow zoom, 30fps) + cover.jpg

Usage:
  python3 scripts/render_reel.py entry.json out_dir [--voice en-US-AndrewNeural] [--rate +6%] [--no-photo]

entry.json fields used: hook, voiceover, cover_text (optional), series (optional),
subject / subject_alt (optional — who the reel is about; drives the photo),
photo (optional — a pre-resolved subject_image.resolve() dict; skips the lookup).

With a photo: the top ~60% is the subject, feathered into a blurred/darkened copy of
itself; captions sit in the lower band with the credit line above the handle.
Without one (no subject, or nothing usable on Commons): the original gradient card.
Everything is original audio (voice only) — no licensed music, so nothing gets muted.
"""
import argparse
import asyncio
import json
import os
import random
import subprocess
import sys

import edge_tts
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subject_image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1080, 1920
FPS = 30
WORDS_PER_CARD = 3
PHOTO_H = 1150      # height of the sharp photo band at the top
FEATHER = 340       # how much of the band's bottom edge fades into the backdrop
CREDIT_Y = H - 385  # credit line sits just above the divider/handle

THEMES = [
    ((10, 6, 12), (72, 10, 28), (6, 4, 8), (200, 60, 84), (245, 236, 238)),
    ((6, 8, 14), (14, 26, 58), (4, 6, 10), (96, 148, 220), (232, 238, 248)),
    ((8, 10, 8), (16, 44, 28), (4, 8, 6), (96, 190, 130), (232, 244, 236)),
    ((12, 10, 6), (64, 44, 12), (8, 6, 4), (222, 168, 62), (248, 242, 230)),
    ((10, 8, 12), (44, 20, 60), (6, 4, 10), (168, 110, 220), (240, 234, 246)),
    ((10, 10, 10), (36, 36, 40), (5, 5, 6), (200, 200, 205), (244, 244, 246)),
]


def font(size, weight="ExtraBold"):
    for p in (os.path.join(ROOT, "fonts", f"Montserrat-{weight}.ttf"),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def background(theme_idx, seed):
    top, mid, bot, accent, _ = THEMES[theme_idx % len(THEMES)]
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
    ImageDraw.Draw(glow).ellipse([W // 2 - 460, H // 2 - 420, W // 2 + 460, H // 2 + 300],
                                 fill=tuple(min(255, int(c * 0.6)) for c in accent))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(220)), 0.30)
    px = img.load()
    rnd = random.Random(seed)
    for _ in range(40000):
        x, y = rnd.randrange(W), rnd.randrange(H)
        r, g, b = px[x, y]
        n = rnd.randint(-8, 8)
        px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    return img


def cover_fit(src, w, h, focus=0.5):
    """Scale so the image covers w x h, then crop. focus = where to keep vertically (0 top, 1 bottom)."""
    k = max(w / src.width, h / src.height)
    img = src.resize((max(w, round(src.width * k)), max(h, round(src.height * k))), Image.LANCZOS)
    x = (img.width - w) // 2
    y = round((img.height - h) * focus)
    return img.crop((x, y, x + w, y + h))


def photo_background(photo_path):
    """Subject photo: sharp band up top feathered into a blurred, darkened full-bleed copy."""
    src = Image.open(photo_path).convert("RGB")
    back = cover_fit(src, W, H).filter(ImageFilter.GaussianBlur(46))
    back = ImageEnhance.Brightness(back).enhance(0.32)
    fg = cover_fit(src, W, PHOTO_H, focus=0.3)  # faces live in the upper part of most portraits
    mask = Image.new("L", (W, PHOTO_H), 255)
    md = ImageDraw.Draw(mask)
    for i in range(FEATHER):
        a = int(255 * (1 - i / FEATHER) ** 1.5)
        md.line([(0, PHOTO_H - FEATHER + i), (W, PHOTO_H - FEATHER + i)], fill=a)
    back.paste(fg, (0, 0), mask)
    # a light top vignette so the series tag reads over bright skies
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).rectangle([0, 0, W, 300], fill=110)
    vig = vig.filter(ImageFilter.GaussianBlur(90))
    back.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), vig)
    return back


def load_photo(e, out_dir, enabled=True):
    """Resolve + download the subject photo for this entry. None → gradient fallback."""
    if not enabled:
        return None
    info = e.get("photo")
    if not info and e.get("subject"):
        info = subject_image.resolve(e["subject"], e.get("subject_alt"))
    if not info:
        return None
    dest = os.path.join(out_dir, "photo.jpg")
    try:
        subject_image.fetch(info["url"], dest)
        Image.open(dest).verify()
    except Exception as ex:
        print(f"photo fetch failed ({info.get('file')}): {ex}", file=sys.stderr)
        return None
    return {**info, "local": dest}


def wrap(d, text, f, max_w):
    lines, line = [], []
    for w in text.split():
        trial = " ".join(line + [w])
        if d.textlength(trial, font=f) <= max_w or not line:
            line.append(w)
        else:
            lines.append(" ".join(line))
            line = [w]
    lines.append(" ".join(line))
    return lines


def fit(d, text, max_w, max_h, start, floor, weight="ExtraBold", lh=1.12):
    size = start
    while size > floor:
        f = font(size, weight)
        lines = wrap(d, text, f, max_w)
        if len(lines) * int(size * lh) <= max_h:
            return f, lines, int(size * lh)
        size -= 6
    f = font(floor, weight)
    return f, wrap(d, text, f, max_w), int(floor * lh)


def chrome(img, theme_idx, series, handle, credit=None):
    """Static furniture: series tag top, handle bottom, photo credit above it."""
    _, _, _, accent, text_col = THEMES[theme_idx % len(THEMES)]
    d = ImageDraw.Draw(img)
    tf = font(38, "Medium")
    tw = d.textlength(series, font=tf)
    if credit:  # over a photo the tag needs its own ground
        d.rounded_rectangle([(W - tw) / 2 - 34, 172, (W + tw) / 2 + 34, 250], radius=39, fill=(0, 0, 0))
    d.text(((W - tw) / 2, 190), series, font=tf, fill=accent)
    d.line([(W // 2 - 110, H - 300), (W // 2 + 110, H - 300)], fill=accent, width=2)
    hf = font(40, "Medium")
    ht = f"@{handle}"
    hw = d.textlength(ht, font=hf)
    d.text(((W - hw) / 2, H - 262), ht, font=hf, fill=text_col)
    if credit:
        cf = font(24, "Medium")
        text = credit
        while d.textlength(text, font=cf) > W - 120 and len(text) > 20:
            text = text[:-4].rstrip() + "…"
        cw = d.textlength(text, font=cf)
        d.text(((W - cw) / 2 + 1, CREDIT_Y + 1), text, font=cf, fill=(0, 0, 0))
        d.text(((W - cw) / 2, CREDIT_Y), text, font=cf, fill=(178, 178, 184))


def text_band(has_photo):
    """(center_y, max_h, start_size, floor_size) for the caption block."""
    if has_photo:
        top, bot = PHOTO_H - 110, CREDIT_Y - 30
        return (top + bot) // 2, bot - top, 118, 58
    return H // 2 - 40, 900, 150, 72


def draw_lines(d, lines, f, lh, center_y, colors):
    y = center_y - (len(lines) * lh) // 2
    for i, ln in enumerate(lines):
        lw = d.textlength(ln, font=f)
        x = (W - lw) / 2
        d.text((x + 6, y + 8), ln, font=f, fill=(0, 0, 0))  # soft shadow then face
        d.text((x, y), ln, font=f, fill=colors[i])
        y += lh
    return y


def card(bg, theme_idx, words, series, handle, credit=None):
    _, _, _, accent, text_col = THEMES[theme_idx % len(THEMES)]
    img = bg.copy()
    chrome(img, theme_idx, series, handle, credit)
    d = ImageDraw.Draw(img)
    cy, max_h, start, floor = text_band(credit is not None)
    f, lines, lh = fit(d, " ".join(words).upper(), W - 200, max_h, start, floor)
    # last line in accent = the "beat"
    colors = [accent if i == len(lines) - 1 and len(lines) > 1 else text_col for i in range(len(lines))]
    draw_lines(d, lines, f, lh, cy, colors)
    return img


def cover(bg, theme_idx, hook, series, handle, credit=None):
    _, _, _, accent, text_col = THEMES[theme_idx % len(THEMES)]
    img = bg.copy()
    chrome(img, theme_idx, series, handle, credit)
    d = ImageDraw.Draw(img)
    cy, max_h, start, floor = text_band(credit is not None)
    f, lines, lh = fit(d, hook.upper(), W - 180, max_h - 60, start - 12, floor)
    y = draw_lines(d, lines, f, lh, cy - 20, [text_col] * len(lines))
    d.rectangle([W // 2 - 90, y + 30, W // 2 + 90, y + 42], fill=accent)
    return img


async def tts(text, voice, rate, out_mp3):
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words = []
    with open(out_mp3, "wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append((chunk["offset"] / 1e7, (chunk["offset"] + chunk["duration"]) / 1e7, chunk["text"]))
    return words


def beats(words, script, n=WORDS_PER_CARD):
    """Group word timings into caption beats; break on phrase punctuation.

    edge-tts strips punctuation from WordBoundary text, so line the timings up
    with the script's own tokens (same count in practice) to know where the
    commas and full stops are.
    """
    tokens = script.split()
    norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())  # noqa: E731
    out, cur, ti = [], [], 0
    for w in words:
        # greedy align: edge-tts may merge/split around hyphens, dots and numbers
        tok, nw = w[2], norm(w[2])
        for look in range(ti, min(ti + 3, len(tokens))):
            nt = norm(tokens[look])
            if nw and nt and (nt == nw or nt.startswith(nw) or nw.startswith(nt)):
                tok, ti = tokens[look], look + 1
                break
        shown = tok.strip("“”\"'()[]").rstrip(".,;:!?—-…")
        if not shown:
            shown = w[2]
        cur.append((w[0], w[1], shown))
        if len(cur) >= n or tok.rstrip("”\"')")[-1:] in ".!?,:;—…":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [(b[0][0], b[-1][1], [w[2] for w in b]) for b in out]


def probe_duration(path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entry")
    ap.add_argument("out")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--rate", default=None)
    ap.add_argument("--theme", type=int, default=None)
    ap.add_argument("--no-photo", action="store_true", help="force the gradient card")
    a = ap.parse_args()

    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    rc = cfg.get("reels", {})
    voice = a.voice or rc.get("voice", "en-US-AndrewNeural")
    rate = a.rate or rc.get("rate", "+6%")
    handle = cfg["handle"]
    e = json.load(open(a.entry))
    series = (e.get("series") or rc.get("series_tag") or cfg.get("series_tag", "")).upper()
    os.makedirs(a.out, exist_ok=True)
    seed = sum(map(ord, e.get("title", e["hook"])))
    theme_idx = a.theme if a.theme is not None else seed % len(THEMES)

    voice_mp3 = os.path.join(a.out, "voice.mp3")
    words = asyncio.run(tts(e["voiceover"], voice, rate, voice_mp3))
    if not words:
        sys.exit("edge-tts returned no word boundaries")
    audio_len = probe_duration(voice_mp3)
    tail = 0.9  # breathing room after the last word
    total = audio_len + tail

    photo = load_photo(e, a.out, enabled=not a.no_photo)
    credit = photo["credit"] if photo else None
    bg = photo_background(photo["local"]) if photo else background(theme_idx, seed)
    if photo and not e.get("photo"):
        # remember exactly which photo/credit went out (the workflow commits the staged entry)
        e["photo"] = {k: photo[k] for k in ("file", "url", "page", "license", "artist", "credit", "subject", "via") if k in photo}
        json.dump(e, open(a.entry, "w"), indent=1, ensure_ascii=False)
    cards_dir = os.path.join(a.out, "cards")
    os.makedirs(cards_dir, exist_ok=True)
    seq = beats(words, e["voiceover"])
    # stretch beats to be contiguous so there is never a blank frame
    concat = os.path.join(a.out, "cards.txt")
    with open(concat, "w") as fh:
        for i, (start, end, ws) in enumerate(seq):
            nxt = seq[i + 1][0] if i + 1 < len(seq) else total
            begin = start if i else 0.0  # first card also covers the lead-in silence
            dur = max(0.15, nxt - begin)
            p = os.path.abspath(os.path.join(cards_dir, f"c{i:03d}.png"))  # concat resolves relative to the list file
            card(bg, theme_idx, ws, series, handle, credit).save(p)
            fh.write(f"file '{p}'\nduration {dur:.3f}\n")
        fh.write(f"file '{p}'\n")  # concat demuxer quirk: repeat last file

    cover_path = os.path.join(a.out, "cover.jpg")
    cover(bg, theme_idx, e.get("cover_text") or e["hook"], series, handle, credit).save(cover_path, quality=92)

    out_mp4 = os.path.join(a.out, "reel.mp4")
    zoom = 0.06  # 6% push-in across the whole clip
    # crop/zoompan can't take t in w/h; scale with eval=frame can, then center-crop back to 9:16
    vf = (f"fps={FPS},scale=w=iw*(1+{zoom}*t/{total:.3f}):h=ih*(1+{zoom}*t/{total:.3f}):eval=frame:flags=bicubic,"
          f"crop={W}:{H},format=yuv420p")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", concat,
                    "-i", voice_mp3, "-vf", vf, "-t", f"{total:.3f}",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-r", str(FPS),
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", out_mp4], check=True)
    meta = {"reel": out_mp4, "cover": cover_path, "duration": round(probe_duration(out_mp4), 2),
            "words": len(words), "cards": len(seq), "theme": theme_idx, "voice": voice,
            "photo": (photo or {}).get("file"), "credit": credit}
    json.dump(meta, open(os.path.join(a.out, "render.json"), "w"), indent=1)
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
