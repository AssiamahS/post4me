#!/usr/bin/env python3
"""Render a RealjN-style sample reel: the record, then the flip, with the actual music playing.

  hook card (2-3s)  ─ freeze frame of the first flip's video, big two-line hook, one spoken line
  per pair:
     ORIGINAL clip   ─ the sampled record's own music video, center-cropped 9:16, music playing,
                       lower-third at the top: ARTIST (YEAR) / "SONG" / PROD. X
                       halfway through: the technique label (*LOOP*, *CHOP*, *PITCHED UP* …)
     FLIP clip       ─ the rap record's video at the moment the loop first drops, same text block
  hard cuts, ~5.5s a clip, 4 pairs ≈ 47s.

Where the sample sits is FOUND, not guessed: both songs' audio is pulled, a chroma (12-pitch-class)
fingerprint of the flip is slid across the original (±3 semitones, ±10% tempo) to locate the sampled
passage, then that passage is slid across the flip to find the first bar where the loop plays.
Reversed samples can't be matched that way → pin "start" on both sides and set "locate": false.

YouTube ids: pinned per clip ("yt"). Missing ones are resolved from a search that only accepts the
artist's own channel / a "- Topic" auto-channel / VEVO / "official" uploads, and the pick (title +
channel) is printed into the preview text so it can be vetoed before anything posts.

  ~/.yt-dlp-venv/bin/python scripts/sample_reel.py roadmap/samples/wu-tang-soul.json build/samples/wu-tang-soul
Writes reel.mp4, cover.jpg, strip.jpg (6 frames side by side), render.json and pins yt/start back
into the entry file.
"""
import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sample_sources  # noqa: E402

CACHE = os.path.expanduser("~/.post4me/media")
MIN_CLIP, MAX_CLIP = 4.0, 9.0   # a clip is as long as the phrase, inside these
W, H = 1080, 1920
FPS = 30
SR = 22050
CLIP = 5.5            # seconds per clip
PAD = 1.5             # extra seconds fetched either side of a clip
RED = (255, 46, 46)
YELLOW = (255, 214, 50)
WHITE = (255, 255, 255)
BAD = re.compile(r"\b(lyrics?|reaction|cover|live|slowed|sped ?up|instrumental|karaoke|remix|8d|nightcore|"
                 r"tutorial|sample breakdown|hour|loop|mashup|tiktok|shorts?)\b", re.I)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def font(size, weight="ExtraBold"):
    return ImageFont.truetype(os.path.join(ROOT, "fonts", f"Montserrat-{weight}.ttf"), size)


def sh(*cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


# ---------- youtube ----------

YTDLP = ["yt-dlp", "--ignore-config", "--no-warnings", "--remote-components", "ejs:github",
         "--no-match-filters", "--no-download-archive"]


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("&", "and")).strip()


def resolve_yt(artist, song):
    """Pick the official upload for artist/song. Returns (id, title, channel) or None."""
    out = sh(*YTDLP, "--flat-playlist", "-J", f"ytsearch8:{artist} {song}")
    best = None
    for e in json.loads(out).get("entries", []):
        title, chan = e.get("title") or "", (e.get("channel") or e.get("uploader") or "")
        dur = e.get("duration") or 0
        if not (75 <= dur <= 720) or BAD.search(title):
            continue
        s = 0
        ns, nt, na, nc = norm(song), norm(title), norm(artist), norm(chan)
        if ns in nt:
            s += 3
        elif sum(w in nt for w in ns.split()) >= max(1, len(ns.split()) - 1):
            s += 2
        else:
            continue
        if nc == na or na in nc:
            s += 3
        elif nc.endswith("topic") or "vevo" in nc:
            s += 3
        elif "official" in nt:
            s += 1
        else:
            continue  # fan uploads are how you end up with the wrong record
        s += min(2, math.log10(max(1, e.get("view_count") or 1)) / 4)
        if not best or s > best[0]:
            best = (s, e["id"], title, chan)
    return best[1:] if best else None


def fetch_audio(yt):
    dst = os.path.join(CACHE, f"{yt}.m4a")
    if not os.path.exists(dst):
        os.makedirs(CACHE, exist_ok=True)
        sh(*YTDLP, "-f", "ba[ext=m4a]/ba", "-o", dst, f"https://www.youtube.com/watch?v={yt}")
    return dst


def fetch_video_section(yt, start, dur):
    a, b = max(0.0, start - PAD), start + dur + PAD
    dst = os.path.join(CACHE, f"{yt}_{a:.1f}_{b:.1f}.mp4")
    if not os.path.exists(dst):
        os.makedirs(CACHE, exist_ok=True)
        sh(*YTDLP, "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
           "--download-sections", f"*{a:.2f}-{b:.2f}", "--force-keyframes-at-cuts",
           "--merge-output-format", "mp4", "-o", dst, f"https://www.youtube.com/watch?v={yt}")
    return dst, a


def decode(path, sr=SR):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


# ---------- chroma matching ----------

N_FFT, HOP = 4096, 2048
FRAME_S = HOP / SR


def chroma(y):
    win = np.hanning(N_FFT)
    n = max(0, (len(y) - N_FFT) // HOP + 1)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    keep = (freqs > 55) & (freqs < 4200)
    pcs = (np.round(12 * np.log2(freqs[keep] / 440.0) + 69).astype(int)) % 12
    out = np.zeros((n, 12), dtype=np.float32)
    for i in range(n):
        seg = y[i * HOP:i * HOP + N_FFT] * win
        mag = np.abs(np.fft.rfft(seg))[keep]
        out[i] = np.bincount(pcs, weights=mag, minlength=12)
    out = np.log1p(out * 10)
    out -= out.mean(axis=1, keepdims=True)
    out /= (np.linalg.norm(out, axis=1, keepdims=True) + 1e-6)
    return out


def stretch(q, k):
    idx = np.clip(np.round(np.arange(0, len(q), k)).astype(int), 0, len(q) - 1)
    return q[idx]


def corr_all(target, q):
    """Correlation of q against every alignment in target (sum over the 12 pitch classes)."""
    scores = np.zeros(len(target) - len(q) + 1)
    for c in range(12):
        scores += np.correlate(target[:, c], q[:, c], mode="valid")
    return scores / len(q)


def peaks(scores, k=6, sep_frames=40):
    """Top-k non-overlapping peaks → [(frame, value)] in descending value."""
    s = scores.copy()
    out = []
    for _ in range(k):
        i = int(np.argmax(s))
        if s[i] <= -1:
            break
        out.append((i, float(s[i])))
        s[max(0, i - sep_frames):i + sep_frames] = -1
    return out


def locate(orig_audio, flip_audio, win=6.0, step=1.5):
    """Which 6s of the original is the sample, and where in the flip does it first play.

    Every window of the original is correlated against the whole flip (±3 semitones, 0.9–1.1x
    tempo). A real sample is a LOOP, so it lines up again and again across the flip: the score
    is the mean of the top-6 separate peaks, which noise can't fake. The flip clip starts at the
    earliest of those peaks (first verse, loop running) rather than a skit intro.
    """
    yo, yf = decode(orig_audio), decode(flip_audio)
    co, cf = chroma(yo), chroma(yf)
    wf, sf = int(win / FRAME_S), int(step / FRAME_S)
    best = None
    for k in (0.9, 0.95, 1.0, 1.05, 1.1):
        for shift in range(-3, 4):
            cfs = np.roll(cf, shift, axis=1)
            for start in range(0, max(1, len(co) - wf), sf):
                q = stretch(co[start:start + wf], k)
                if len(q) >= len(cfs):
                    continue
                sc = corr_all(cfs, q)
                pk = peaks(sc)
                v = float(np.mean([x[1] for x in pk]))
                if best is None or v > best[0]:
                    best = (v, start * FRAME_S, shift, k, pk)
    v, o_start, shift, k, pk = best
    f_start = min(x[0] for x in pk) * FRAME_S
    return {"orig_start": round(o_start, 2), "flip_start": round(f_start, 2), "score": round(v, 3),
            "semitones": shift, "tempo": k, "orig_len": round(len(yo) / SR, 1), "flip_len": round(len(yf) / SR, 1)}


# ---------- musical timing ----------

def onsets(y):
    """Spectral-flux onset strength per 512-sample hop (~23ms) → (envelope, hop_seconds)."""
    n_fft, hop = 1024, 512
    win = np.hanning(n_fft)
    n = max(1, (len(y) - n_fft) // hop + 1)
    prev = None
    env = np.zeros(n, dtype=np.float32)
    for i in range(n):
        mag = np.abs(np.fft.rfft(y[i * hop:i * hop + n_fft] * win))
        if prev is not None:
            env[i] = np.maximum(mag - prev, 0).sum()
        prev = mag
    if env.max() > 0:
        env /= env.max()
    return env, hop / SR


def snap(y, t, before=0.6, after=1.2):
    """Move t to the strongest onset in [t-before, t+after] so the cut lands on a hit."""
    env, hs = onsets(y[max(0, int((t - before) * SR)):int((t + after) * SR)])
    if len(env) < 3:
        return t
    return round(max(0.0, t - before) + int(np.argmax(env)) * hs, 2)


def verify(co, cf, o_start, f_hint, win=6.0):
    """Score a documented relationship: the original's window at o_start against the flip, with
    the finest tempo/pitch grid. Returns (score, shift, tempo, flip_at, second_best) — second_best
    is the best score at a clearly different (tempo, shift), the margin is the evidence."""
    q0 = co[int(o_start / FRAME_S):int((o_start + win) / FRAME_S)]
    if len(q0) < 10:
        return 0.0, 0, 1.0, f_hint, 0.0
    results = []
    for k in np.arange(0.90, 1.125, 0.01):
        qs = stretch(q0, float(k))
        if len(qs) >= len(cf):
            continue
        for shift in range(-4, 5):
            sc = corr_all(cf, np.roll(qs, shift, axis=1))
            pk = peaks(sc)
            results.append((float(np.mean([x[1] for x in pk])), shift, round(float(k), 2), pk))
    results.sort(key=lambda r: -r[0])
    best = results[0]
    second = next((r[0] for r in results if abs(r[2] - best[2]) >= 0.04 or abs(r[1] - best[1]) >= 2), 0.0)
    # flip position: the earliest strong peak near the documented hint (or anywhere if no hint)
    pk = best[3]
    cands = sorted(x[0] * FRAME_S for x in pk)
    if f_hint is not None:
        near = [c for c in cands if c >= f_hint - 2.0]
        flip_at = min(near) if near else cands[0]
    else:
        flip_at = cands[0]
    return best[0], best[1], best[2], round(flip_at, 2), second


def phrase_length(co, cf, o_start, f_start, first_score):
    """Extend from the sample's start in 1.5s steps while the original keeps matching the flip at
    the same offset; the phrase ends where it stops. Clamped to MIN_CLIP..MAX_CLIP."""
    step = 1.5
    length = step
    region = cf[int(f_start / FRAME_S):int((f_start + MAX_CLIP + 2) / FRAME_S)]
    while length + step <= MAX_CLIP:
        a = int((o_start + length) / FRAME_S)
        chunk = co[a:a + int(step / FRAME_S)]
        if len(chunk) < 5 or len(chunk) >= len(region):
            break
        sc = corr_all(region, chunk)
        if sc.max() < 0.7 * max(first_score, 0.3):
            break
        length += step
    return float(min(MAX_CLIP, max(MIN_CLIP, length)))


def technique_label(doc, score, shift, tempo, second, reversed_gain):
    """Only say what the evidence supports. Documented element type first, then measured
    speed/pitch when the best (tempo, pitch) combination beats the runner-up by a margin."""
    lines = []
    typ = ((doc or {}).get("type") or "").lower()
    el = ((doc or {}).get("element") or "").lower()
    if "interpolation" in typ or "replay" in typ:
        lines.append("*REPLAYED*")
    elif "vocal" in el:
        lines.append("*VOCAL SAMPLE*")
    elif "drum" in el:
        lines.append("*DRUM BREAK*")
    elif "hook" in el or "riff" in el:
        lines.append("*THE RIFF*")
    elif "bass" in el:
        lines.append("*BASSLINE*")
    if reversed_gain >= 0.05:
        lines.append("*REVERSED*")
    evidence = score >= 0.45 and (score - second) >= 0.03
    if evidence:
        if abs(tempo - 1.0) >= 0.03:
            lines.append(f"[{'+' if tempo > 1 else ''}{round((tempo - 1) * 100)}% SPEED]")
        expected = round(12 * math.log2(tempo))
        extra = shift - expected
        if abs(extra) >= 1:
            lines.append(f"[PITCHED {'UP' if extra > 0 else 'DOWN'} {abs(extra)} SEMITONE{'S' if abs(extra) > 1 else ''}]")
    if not lines:
        lines.append("*LOOPED*" if (doc or {}).get("throughout") else "*SAMPLED*")
    return "\n".join(lines[:3])


def reversed_gain(co, cf, o_start, win=6.0):
    q = co[int(o_start / FRAME_S):int((o_start + win) / FRAME_S)]
    if len(q) < 10 or len(q) >= len(cf):
        return 0.0
    fwd = float(np.mean([x[1] for x in peaks(corr_all(cf, q))]))
    rev = float(np.mean([x[1] for x in peaks(corr_all(cf, q[::-1]))]))
    return rev - fwd


# ---------- drawing ----------

def outlined(d, xy, text, f, fill, stroke=7):
    d.text(xy, text, font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))


def centered_lines(d, lines, y, gap=14):
    for text, f, col in lines:
        w = d.textlength(text, font=f)
        outlined(d, ((W - w) / 2, y), text, f, col)
        y += f.size + gap
    return y


def fit_font(d, text, max_w, start, floor=44):
    size = start
    while size > floor and d.textlength(text, font=font(size)) > max_w:
        size -= 4
    return font(size)


def credit_overlay(side, path):
    """Transparent PNG: ARTIST (YEAR) / "SONG" / PROD. X at the top of the frame."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    artist = f"{side['artist']} ({side['year']})".upper()
    song = f"“{side['song']}”".upper()
    lines = [(artist, fit_font(d, artist, W - 140, 62), WHITE), (song, fit_font(d, song, W - 140, 62), RED)]
    if side.get("prod"):
        prod = f"PROD. {side['prod']}".upper()
        # long producer lists wrap onto a second line
        pf = fit_font(d, prod, W - 140, 56, 40)
        if d.textlength(prod, font=pf) > W - 140:
            words, cur, out = prod.split(), [], []
            for w in words:
                if d.textlength(" ".join(cur + [w]), font=pf) > W - 140 and cur:
                    out.append(" ".join(cur))
                    cur = [w]
                else:
                    cur.append(w)
            out.append(" ".join(cur))
            lines += [(ln, pf, WHITE) for ln in out]
        else:
            lines.append((prod, pf, WHITE))
    centered_lines(d, lines, 210)
    img.save(path)
    return path


def technique_overlay(label, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    parts = label.split("\n")
    lines = [(parts[0].upper(), font(66), WHITE)] + [(p.upper(), font(56), YELLOW) for p in parts[1:]]
    total = sum(f.size + 14 for _, f, _ in lines)
    centered_lines(d, lines, H // 2 - total // 2 + 40)
    img.save(path)
    return path


def hook_card(frame_path, hook, handle, path):
    src = Image.open(frame_path).convert("RGB")
    k = max(W / src.width, H / src.height)
    src = src.resize((round(src.width * k), round(src.height * k)), Image.LANCZOS)
    x, y = (src.width - W) // 2, (src.height - H) // 2
    img = src.crop((x, y, x + W, y + H)).point(lambda v: int(v * 0.7))
    d = ImageDraw.Draw(img)
    lines = hook.split("\n")
    fs = [fit_font(d, ln, W - 120, 118, 60) for ln in lines]
    y = 250
    for i, ln in enumerate(lines):
        w = d.textlength(ln, font=fs[i])
        outlined(d, ((W - w) / 2, y), ln, fs[i], RED if i == len(lines) - 1 and len(lines) > 1 else WHITE, stroke=9)
        y += fs[i].size + 18
    hf = font(40, "Medium")
    hw = d.textlength(f"@{handle}", font=hf)
    outlined(d, ((W - hw) / 2, H - 260), f"@{handle}", hf, (230, 230, 230), stroke=4)
    img.save(path, quality=92)
    return path


def handle_overlay(handle, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    hf = font(34, "Medium")
    t = f"@{handle}"
    w = d.textlength(t, font=hf)
    d.text(((W - w) / 2, H - 230), t, font=hf, fill=(255, 255, 255, 170), stroke_width=3, stroke_fill=(0, 0, 0, 170))
    img.save(path)
    return path


# ---------- segments ----------

VCODEC = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS), "-pix_fmt", "yuv420p"]
ACODEC = ["-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]


def clip_segment(src, offset, dur, overlays, out, push=False):
    """Center-crop the music video to 9:16, burn the text, fade the audio edges.
    push=True adds a 6% push-in for static album-art uploads."""
    inputs = ["-ss", f"{offset:.3f}", "-t", f"{dur:.3f}", "-i", src]
    for p, _ in overlays:
        inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", p]
    if push:
        fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
              f"scale=w=iw*(1+0.06*t/{dur:.3f}):h=ih*(1+0.06*t/{dur:.3f}):eval=frame:flags=bicubic,crop={W}:{H}[v0]")
    else:
        fc = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}[v0]"
    cur = "v0"
    for i, (_, enable) in enumerate(overlays, start=1):
        en = f":enable='gte(t,{enable})'" if enable else ""
        fc += f";[{cur}][{i}:v]overlay=0:0:format=auto{en}[v{i}]"
        cur = f"v{i}"
    fc += (f";[0:a]aresample=44100,afade=t=in:d=0.12,afade=t=out:st={dur - 0.18:.3f}:d=0.18,"
           f"loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", fc, "-map", f"[{cur}]", "-map", "[a]",
                    "-t", f"{dur:.3f}", *VCODEC, *ACODEC, "-movflags", "+faststart", out], check=True)
    return out


async def tts(text, voice, rate, out_mp3):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(out_mp3)


def hook_segment(card, voice_mp3, out):
    dur = float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", voice_mp3)) + 0.45
    dur = max(2.2, dur)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{dur:.3f}", "-i", card, "-i", voice_mp3,
                    "-filter_complex",
                    f"[0:v]scale={W * 1.08:.0f}:{H * 1.08:.0f},zoompan=z='1+0.06*on/{dur * FPS:.0f}':d=1:x='iw/2-(iw/zoom/2)'"
                    f":y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},format=yuv420p[v];"
                    f"[1:a]aresample=44100,apad,atrim=0:{dur:.3f},loudnorm=I=-16:TP=-1.5[a]",
                    "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", *VCODEC, *ACODEC, out], check=True)
    return out, dur


def frame_at(video, t, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", out], check=True)
    return out


def concat(parts, out):
    lst = out + ".txt"
    with open(lst, "w") as fh:
        for p in parts:
            fh.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                    "-movflags", "+faststart", out], check=True)
    return out


def strip(video, dur, out, n=6):
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        frames.append(frame_at(video, t, out + f".{i}.jpg"))
    ims = [Image.open(f).resize((270, 480)) for f in frames]
    sheet = Image.new("RGB", (270 * n, 480))
    for i, im in enumerate(ims):
        sheet.paste(im, (270 * i, 0))
    sheet.save(out, quality=85)
    for f in frames:
        os.remove(f)
    return out


# ---------- main ----------

def prepare_pair(pair, i, cfg):
    """Discovery → verification → timing for one pair. Mutates pair, returns (notes, confidence)."""
    notes = []
    o, f = pair["orig"], pair["flip"]
    for side, sd in (("orig", o), ("flip", f)):
        if not sd.get("yt"):
            r = resolve_yt(sd["artist"], sd["song"])
            if not r:
                raise SystemExit(f"pair {i}: no official upload found for {sd['artist']} – {sd['song']}")
            sd["yt"], sd["yt_title"], sd["yt_channel"] = r
            notes.append(f"  {side}: picked {r[1]!r} ({r[2]})")

    # 1. documented relationship (WhoSampled via Dia, Wikipedia as cross-check)
    doc = pair.get("documented")
    if doc is None:
        doc = sample_sources.lookup(f["artist"], f["song"], o["artist"], o["song"])
        pair["documented"] = doc or {"source": None}
    doc = doc if doc and doc.get("source") else None
    if doc:
        where = ""
        if doc.get("orig_at") is not None:
            where = f" (original {doc['orig_at'] // 60}:{doc['orig_at'] % 60:02d} → flip {doc.get('flip_at', 0) // 60}:{doc.get('flip_at', 0) % 60:02d})"
        notes.append(f"  documented: {doc['source']}{' + wikipedia' if doc.get('wikipedia') else ''}{where}"
                     + (f" · {doc['type']}" if doc.get("type") else ""))
        if doc.get("flip_producers") and not f.get("prod_locked"):
            f["prod"] = " & ".join(doc["flip_producers"][:3])
        if doc.get("orig_producers") and not o.get("prod_locked"):
            o["prod"] = " & ".join(doc["orig_producers"][:3])
        if doc.get("orig_year"):
            o["year"] = doc["orig_year"]
    else:
        notes.append("  NOT DOCUMENTED anywhere I can read — audio match only")

    # 2. audio verification + localisation
    yo, yf = decode(fetch_audio(o["yt"])), decode(fetch_audio(f["yt"]))
    co, cf = chroma(yo), chroma(yf)
    if pair.get("locate", True):
        if doc and doc.get("orig_at") is not None:
            o_start = float(doc["orig_at"])
            score, shift, tempo, f_at, second = verify(co, cf, o_start, doc.get("flip_at"))
            # WhoSampled times are whole seconds: let the audio nudge the start within the bar
            best_local = (score, o_start)
            for dt in (-1.0, -0.5, 0.5, 1.0, 1.5, 2.0):
                sc, *_ = verify(co, cf, o_start + dt, doc.get("flip_at"))
                if sc > best_local[0] + 0.01:
                    best_local = (sc, o_start + dt)
            if best_local[1] != o_start:
                o_start = best_local[1]
                score, shift, tempo, f_at, second = verify(co, cf, o_start, doc.get("flip_at"))
            how = "verified"
        else:
            loc = locate(fetch_audio(o["yt"]), fetch_audio(f["yt"]))
            o_start = loc["orig_start"]
            score, shift, tempo, f_at, second = verify(co, cf, o_start, None)
            how = "audio-only"
        rg = reversed_gain(co, cf, o_start)
        if o.get("start") is None:
            o["start"] = snap(yo, o_start)
        if f.get("start") is None:
            f["start"] = snap(yf, f_at)
        length = phrase_length(co, cf, o["start"], f["start"], score)
        o.setdefault("dur", length)
        f.setdefault("dur", length)
        pair["match"] = {"how": how, "score": round(score, 3), "second": round(second, 3), "semitones": shift,
                         "tempo": tempo, "reversed_gain": round(rg, 3), "phrase_s": length}
        if not pair.get("technique_locked"):
            pair["technique"] = technique_label(doc, score, shift, tempo, second, rg)
        notes.append(f"  {how}: match {score:.2f} (runner-up {second:.2f}, tempo x{tempo}, {shift:+d} st) → "
                     f"original @{o['start']}s, flip @{f['start']}s, {length:.1f}s phrase · label {pair['technique'].replace(chr(10), ' ')}")
    for sd in (o, f):
        sd.setdefault("start", 0.0)
        sd.setdefault("dur", CLIP)
        if "match" not in pair:
            pair["match"] = {"how": "pinned", "score": 0.0}

    # 3. confidence: documented + verified + phrase + source quality
    m = pair["match"]
    conf = 0.0
    if doc:
        conf += 0.45 if doc.get("source") == "whosampled" else 0.30
        if doc.get("wikipedia"):
            conf += 0.10
    conf += min(0.35, m.get("score", 0.0) * 0.6)
    if all((sd.get("yt_channel") or "").lower().endswith("topic") or norm(sd["artist"]) in norm(sd.get("yt_channel") or "")
           or "vevo" in (sd.get("yt_channel") or "").lower() for sd in (o, f)):
        conf += 0.10
    if not pair.get("locate", True):
        conf += 0.15  # hand-pinned starts
    pair["confidence"] = round(min(1.0, conf), 2)
    th = cfg.get("samples", {})
    verdict = ("auto" if conf >= th.get("auto", 0.85) else "preview" if conf >= th.get("min_confidence", 0.5) else "drop")
    notes.append(f"  confidence {pair['confidence']:.2f} → {verdict}")
    return notes, pair["confidence"], verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entry")
    ap.add_argument("out")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    rc = cfg.get("reels", {})
    handle = cfg["handle"]
    e = json.load(open(a.entry))
    os.makedirs(a.out, exist_ok=True)
    notes = []

    keep = []
    for i, pair in enumerate(e["pairs"], start=1):
        log(f"pair {i}: {pair['orig']['artist']} → {pair['flip']['artist']}")
        n, conf, verdict = prepare_pair(pair, i, rc)
        notes += [f"{i}. {pair['orig']['song']} → {pair['flip']['song']}"] + n
        json.dump(e, open(a.entry, "w"), indent=1, ensure_ascii=False)  # pin as we go
        if verdict == "drop":
            notes.append("  DROPPED from this cut (below min_confidence)")
        else:
            keep.append(pair)
    if len(keep) < 2:
        sys.exit("fewer than two pairs cleared the confidence gate — nothing worth posting")
    e["pairs"] = keep

    hand = handle_overlay(handle, os.path.join(a.out, "handle.png"))
    parts, timeline, t = [], [], 0.0
    # hook: freeze frame from the first flip's video at its clip start
    first = e["pairs"][0]["flip"]
    src, base = fetch_video_section(first["yt"], first["start"], first["dur"])
    frame, bright = None, -1
    for j, dt in enumerate((0.2, 1.2, 2.4, 3.6, 4.8)):
        cand = frame_at(src, first["start"] - base + dt, os.path.join(a.out, f"hook_frame{j}.jpg"))
        lum = np.asarray(Image.open(cand).convert("L"), dtype=np.float32).mean()
        if lum > bright:
            frame, bright = cand, lum
    card = hook_card(frame, e["hook"], handle, os.path.join(a.out, "cover.jpg"))
    voice = os.path.join(a.out, "hook.mp3")
    asyncio.run(tts(e.get("hook_voice") or e["hook"].replace("\n", " "), rc.get("voice", "en-US-AndrewNeural"),
                    rc.get("rate", "+6%"), voice))
    seg, d = hook_segment(card, voice, os.path.join(a.out, "seg00.mp4"))
    parts.append(seg)
    timeline.append({"t": 0, "dur": round(d, 2), "what": "hook"})
    t += d

    k = 1
    for pair in e["pairs"]:
        for side in ("orig", "flip"):
            s = pair[side]
            src, base = fetch_video_section(s["yt"], s["start"], s["dur"])
            ov = [(credit_overlay(s, os.path.join(a.out, f"cred{k:02d}.png")), None), (hand, None)]
            if side == "orig" and pair.get("technique"):
                ov.append((technique_overlay(pair["technique"], os.path.join(a.out, f"tech{k:02d}.png")), round(s["dur"] * 0.5, 2)))
            seg = clip_segment(src, s["start"] - base, s["dur"], ov, os.path.join(a.out, f"seg{k:02d}.mp4"),
                               push=(s.get("yt_channel") or "").lower().endswith("topic"))
            parts.append(seg)
            timeline.append({"t": round(t, 2), "dur": s["dur"], "what": f"{side}: {s['artist']} – {s['song']} @{s['start']}s"})
            t += s["dur"]
            k += 1

    reel = concat(parts, os.path.join(a.out, "reel.mp4"))
    total = float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", reel))
    strip(reel, total, os.path.join(a.out, "strip.jpg"))
    meta = {"reel": reel, "cover": card, "strip": os.path.join(a.out, "strip.jpg"), "duration": round(total, 2),
            "pairs": len(e["pairs"]), "timeline": timeline, "notes": notes}
    json.dump(meta, open(os.path.join(a.out, "render.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
