#!/usr/bin/env python3
"""Find a high-quality, properly licensed photo of a reel's subject — no API key.

Source order:
  1. English Wikipedia page image for the subject (REST summary → originalimage)
  2. Wikimedia Commons file search for the subject name (bitmaps only)

Everything on Commons is CC / public domain, so the photo can go in a reel as
long as the credit line is on screen — render_reel.py draws it. Logos, SVGs,
album covers (fair-use, not on Commons) and tiny images are skipped.

  python3 scripts/subject_image.py "Wu-Tang Clan"                 # resolve, print JSON
  python3 scripts/subject_image.py "Wu-Tang Clan" --out build/x.jpg  # resolve + download

Library use: resolve(subject) -> dict|None ; fetch(url, dest) -> dest
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

UA = "post4me/1.0 (https://github.com/AssiamahS/post4me; reel cover photos)"
MIN_W = 700          # below this it looks soft on a 1080-wide card
THUMB_W = 1600       # ask Commons for a resized copy — originals can be 10MB+
BAD_TOKENS = ("logo", "icon", "map", "flag", "signature", "cover", "poster", "screenshot", "diagram")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url):
    return json.loads(_get(url).decode("utf-8"))


def _strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _commons_info(file_title):
    """imageinfo for one Commons file: sized url, dims, license, author."""
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "prop": "imageinfo",
        "titles": file_title, "iiprop": "url|size|extmetadata|mime", "iiurlwidth": THUMB_W,
    })
    d = _json(f"https://commons.wikimedia.org/w/api.php?{q}")
    for p in d.get("query", {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [None])[0]
        if ii:
            return _pack(p["title"], ii)
    return None


def _pack(title, ii):
    em = ii.get("extmetadata", {})
    lic = _strip_html(em.get("LicenseShortName", {}).get("value", "")) or "see Commons"
    artist = _strip_html(em.get("Artist", {}).get("value", "")) or _strip_html(em.get("Credit", {}).get("value", ""))
    artist = re.sub(r"\s+", " ", artist)[:60]
    return {
        "file": title,
        "url": ii.get("thumburl") or ii["url"],
        "width": ii.get("thumbwidth") or ii["width"],
        "height": ii.get("thumbheight") or ii["height"],
        "orig_width": ii["width"],
        "orig_height": ii["height"],
        "mime": ii.get("mime", ""),
        "license": lic,
        "artist": artist,
        "page": ii.get("descriptionurl", ""),
        "credit": f"photo: {artist + ' · ' if artist else ''}{lic} · Wikimedia Commons",
    }


def _ok(info):
    if not info:
        return False
    if not info["mime"].startswith("image/") or info["mime"] in ("image/svg+xml", "image/gif"):
        return False
    if info["orig_width"] < MIN_W or info["orig_height"] < MIN_W * 0.6:
        return False
    low = info["file"].lower()
    return not any(t in low for t in BAD_TOKENS)


def _score(info):
    """Prefer big, roughly portrait-to-square photos (they fill a 9:16 card best)."""
    w, h = info["orig_width"], info["orig_height"]
    ratio = h / w
    shape = 1.0 if 0.75 <= ratio <= 1.6 else (0.7 if 0.5 <= ratio < 0.75 else 0.5)
    return min(w, h) * shape


def wikipedia_page_image(subject):
    t = urllib.parse.quote(subject.replace(" ", "_"))
    try:
        d = _json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}")
    except Exception as ex:  # 404 for unknown titles
        print(f"wikipedia: {subject}: {ex}", file=sys.stderr)
        return None
    if d.get("type") == "disambiguation" or not d.get("originalimage"):
        return None
    src = d["originalimage"]["source"].split("?")[0]
    m = re.search(r"/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/]+)", src)
    if not m:
        return None
    file_title = "File:" + urllib.parse.unquote(m.group(1)).replace("_", " ")
    info = _commons_info(file_title)
    if info:
        info["page"] = d.get("content_urls", {}).get("desktop", {}).get("page", info["page"])
        info["wikipedia"] = d.get("title")
    return info if _ok(info) else None


def _mentions(info, subject):
    """Every real word of the subject must appear in the file title (guards against
    full-text hits like a photo whose description merely name-drops the artist)."""
    words = [w for w in re.findall(r"[a-z0-9]+", subject.lower()) if len(w) > 2 and w not in ("the", "and")]
    title = info["file"].lower()
    return bool(words) and all(w in title for w in words)


def commons_search(subject, limit=15):
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f'intitle:"{subject}" filetype:bitmap', "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime", "iiurlwidth": THUMB_W,
    })
    try:
        d = _json(f"https://commons.wikimedia.org/w/api.php?{q}")
    except Exception as ex:
        print(f"commons: {subject}: {ex}", file=sys.stderr)
        return None
    cands = []
    for p in d.get("query", {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [None])[0]
        if not ii:
            continue
        info = _pack(p["title"], ii)
        if _ok(info) and _mentions(info, subject):
            cands.append(info)
    if not cands:
        return None
    return max(cands, key=_score)


WIDE = 0.55  # h/w below this = panorama/group-shot strip; it crops badly into a 9:16 band


def _try(fn, name):
    try:
        info = fn(name)
    except Exception as ex:
        print(f"{fn.__name__}({name!r}): {ex}", file=sys.stderr)
        return None
    if info:
        info["subject"] = name
        info["via"] = fn.__name__
    return info


def resolve(subject, alt=None):
    """Best photo for `subject`, falling back to `alt`. None if nothing usable.

    Wikipedia's page image wins unless it is a wide strip, in which case a
    Commons title search gets a shot at a taller photo first."""
    fallback = None
    for name in [s for s in (subject, alt) if s]:
        wiki = _try(wikipedia_page_image, name)
        if wiki and wiki["orig_height"] / wiki["orig_width"] >= WIDE:
            return wiki
        fallback = fallback or wiki
        commons = _try(commons_search, name)
        if commons:
            return commons
    return fallback


def fetch(url, dest):
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as fh:
        fh.write(r.read())
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subject")
    ap.add_argument("--alt")
    ap.add_argument("--out", help="download the photo here")
    a = ap.parse_args()
    info = resolve(a.subject, a.alt)
    if not info:
        sys.exit(f"no usable photo for {a.subject!r}")
    if a.out:
        fetch(info["url"], a.out)
        info["local"] = a.out
    print(json.dumps(info, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
