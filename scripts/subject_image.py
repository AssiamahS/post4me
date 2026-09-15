#!/usr/bin/env python3
"""Find a photo of a reel's subject that is guaranteed to BE the subject — no API key.

The only candidate pool is the images that the subject's own English Wikipedia
article uses (the infobox/lead image first, then the rest of the article). A
photo that Wikipedia editors put on "Isaac Hayes" is a photo about Isaac Hayes;
a Commons full-text or title search is not (2026-09-14: `intitle:"Serato"`
returned chef Bruno Serato eating spaghetti, `intitle:"Isaac Hayes"` returned
his Cadillac). Commons search is gone from this file on purpose. Do not add it
back.

Every candidate must also:
  * be a bitmap (no SVG/GIF/PDF), at least MIN_W wide
  * have the subject's surname / band words in the file name
  * not look like an object shot (car, grave, star, plaque, logo, cover, ...)

Everything on Commons is CC / public domain, so the photo can go in a reel as
long as the credit line is on screen — render_reel.py draws it.

  python3 scripts/subject_image.py "Wu-Tang Clan"                   # resolve, print JSON
  python3 scripts/subject_image.py "Wu-Tang Clan" --out build/x.jpg  # resolve + download
  python3 scripts/subject_image.py "Isaac Hayes" --all               # list every candidate + why

Library use: resolve(subject, alt) -> dict|None ; fetch(url, dest) -> dest
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
MIN_W = 480          # a correct 500px portrait beats a gradient; the band is feathered anyway
THUMB_W = 1600       # ask Commons for a resized copy — originals can be 10MB+
# file-name words that mean "an object connected to the subject", not the subject
BAD_TOKENS = ("logo", "icon", "map", "flag", "signature", "cover", "poster", "screenshot", "diagram",
              "cadillac", "car", "house", "home", "grave", "tomb", "memorial", "plaque", "star",
              "statue", "bust", "mural", "sign", "award", "trophy", "building", "studio", "museum",
              "record label", "label", "sleeve", "vinyl", "album", "single", "chart", "ticket", "stamp",
              "walk of fame", "street", "crowd", "audience", "stage", "venue", "billboard", "banner",
              "graffiti", "tattoo", "painting", "drawing", "sketch", "cartoon", "caricature", "wax", "doll",
              "stencil", "figure", "mother", "father", "wife", "husband", "son", "daughter", "brother", "sister",
              # vehicles, buildings and memorabilia named after the subject (Funkmaster Flex's Ford F-150,
              # The Roots' sidewalk marker, Enya's castle, Ice Cube's yearbook page)
              "ford", "truck", "expedition", "chevrolet", "chevy", "motorcycle", "bike", "van", "bus", "jet",
              "marker", "sidewalk", "yearbook", "castle", "mansion", "estate", "medal", "jersey", "sneaker",
              "shoe", "book", "magazine", "newspaper", "ad", "advertisement", "flyer", "receipt", "letter",
              "autograph", "handwriting", "hologram", "exhibit", "exhibition", "gallery", "storefront", "shop")
BAD_RE = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in BAD_TOKENS) + r")\b")
# brands, software and gear: their articles only carry logos / product shots, never a face
NOT_A_PERSON = {"serato", "rekordbox", "pioneer dj", "cdj", "technics", "technics sl-1200", "traktor",
                "rane", "numark", "denon dj", "virtual dj", "ableton", "ableton live", "akai mpc", "mpc",
                "roland tr-808", "tr-808", "sp-1200", "e-mu sp-1200", "turntable", "phonograph", "vinyl record",
                "usb flash drive", "headphones", "dj mixer", "beatport", "spotify", "soundcloud"}
STOP = {"the", "and", "of", "a", "dj", "mc", "rapper", "musician", "band", "group", "producer"}


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url):
    return json.loads(_get(url).decode("utf-8"))


def _strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


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


def _commons_info(file_titles):
    """imageinfo for up to 50 Commons files: sized url, dims, license, author."""
    out = {}
    titles = list(file_titles)
    for i in range(0, len(titles), 50):
        q = urllib.parse.urlencode({
            "action": "query", "format": "json", "prop": "imageinfo", "redirects": 1,
            "titles": "|".join(titles[i:i + 50]), "iiprop": "url|size|extmetadata|mime", "iiurlwidth": THUMB_W,
        })
        d = _json(f"https://commons.wikimedia.org/w/api.php?{q}")
        for p in d.get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [None])[0]
            if ii:
                out[p["title"]] = _pack(p["title"], ii)
    return out


def _name_words(subject):
    words = re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", "", subject.lower()))  # drop "(rapper)"
    return [w for w in words if w not in STOP and len(w) > 1]


def _names_file(file_title, subject):
    """The file name must carry the subject: the surname (last word) for a person,
    or every word for a short band name like "Mobb Deep" / "Wu-Tang Clan"."""
    words = _name_words(subject)
    if not words:
        return False
    title = re.sub(r"[^a-z0-9]+", " ", file_title.lower())
    if len(words) == 1:
        return f" {words[0]} " in f" {title} "
    surname_hit = f" {words[-1]} " in f" {title} " and len(words[-1]) > 3
    all_hit = all(f" {w} " in f" {title} " for w in words)
    return all_hit or (surname_hit and f" {words[0]} " in f" {title} ")


def why_rejected(info, subject):
    if not info["mime"].startswith("image/") or info["mime"] in ("image/svg+xml", "image/gif"):
        return "not a bitmap photo"
    if info["orig_width"] < MIN_W or info["orig_height"] < MIN_W * 0.6:
        return f"too small ({info['orig_width']}x{info['orig_height']})"
    m = BAD_RE.search(re.sub(r"[^a-z0-9]+", " ", info["file"].lower()))
    if m:
        return f"object shot ({m.group(0)!r} in file name)"
    if not any(_names_file(info["file"], name) for name in [subject] + info.get("aliases", [])):
        return "subject name not in file name"
    return None


def _score(info):
    """Prefer big, roughly portrait-to-square photos (they fill a 9:16 card best)."""
    w, h = info["orig_width"], info["orig_height"]
    ratio = h / w
    shape = 1.0 if 0.75 <= ratio <= 1.6 else (0.7 if 0.5 <= ratio < 0.75 else 0.35)
    return min(w, h) * shape


def wikidata_aliases(qid):
    if not qid:
        return []
    q = urllib.parse.urlencode({"action": "wbgetentities", "format": "json", "ids": qid,
                                "props": "aliases", "languages": "en"})
    try:
        d = _json(f"https://www.wikidata.org/w/api.php?{q}")
    except Exception as ex:
        print(f"wikidata {qid}: {ex}", file=sys.stderr)
        return []
    return [a["value"] for a in d.get("entities", {}).get(qid, {}).get("aliases", {}).get("en", [])]


def _alias_ok(title):
    """An alias usable as another name for the subject: "2Pac", "Biggie Smalls", "Christopher
    Wallace" yes; "C. Wallace" no (a bare surname matches relatives), long legal names no."""
    raw = re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", "", title.lower()))
    words = _name_words(title)
    if not words or len(words) > 3 or any(len(w) < 3 for w in words):
        return False
    if len(words) == 1:
        return len(raw) == 1 and len(words[0]) >= 4
    return True


def article_images(subject):
    """(wikipedia title, page url, lead-image file, [file titles], [names]) for the subject's article."""
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "redirects": 1, "titles": subject,
        "prop": "pageprops|pageimages|images", "ppprop": "disambiguation|wikibase_item",
        "piprop": "original", "imlimit": 100,
    })
    d = _json(f"https://en.wikipedia.org/w/api.php?{q}")
    for p in d.get("query", {}).get("pages", {}).values():
        if "missing" in p or "disambiguation" in p.get("pageprops", {}):
            return None
        lead = None
        src = (p.get("original") or {}).get("source", "")
        m = re.search(r"/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/]+)", src)
        if m:
            lead = "File:" + urllib.parse.unquote(m.group(1)).replace("_", " ")
        files = [i["title"] for i in p.get("images", []) if i["title"].lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"))]
        page = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(p["title"].replace(" ", "_"))
        # the subject's own other names from Wikidata ("2Pac", "Biggie Smalls", "Preemo") — NOT
        # Wikipedia redirects, which also carry relatives ("Voletta Wallace" → Biggie's article)
        aliases = wikidata_aliases(p.get("pageprops", {}).get("wikibase_item"))
        return p["title"], page, lead, files, [p["title"]] + [a for a in aliases if _alias_ok(a)]
    return None


def candidates(subject):
    """Every article image with a verdict: [(info, reason_or_None, is_lead)]."""
    art = article_images(subject)
    if not art:
        return None, []
    title, page, lead, files, names = art
    pool = ([lead] if lead else []) + [f for f in files if f != lead]
    infos = _commons_info(pool)
    out = []
    for f in pool:
        info = infos.get(f)
        if not info:
            continue
        info["page"] = page
        info["wikipedia"] = title
        info["aliases"] = names
        out.append((info, why_rejected(info, subject), f == lead))
    return title, out


def resolve_one(subject):
    _, cands = candidates(subject)
    ok = [(info, lead) for info, reason, lead in cands if reason is None]
    if not ok:
        return None
    # the lead image is the editors' chosen portrait — it wins unless it is a wide strip
    lead = [i for i, is_lead in ok if is_lead]
    if lead and lead[0]["orig_height"] / lead[0]["orig_width"] >= 0.6:
        return lead[0]
    return max((i for i, _ in ok), key=_score)


def resolve(subject, alt=None):
    """Best photo for `subject`, falling back to `alt`. None if nothing usable."""
    for name in [s for s in (subject, alt) if s]:
        if name.strip().lower() in NOT_A_PERSON:
            print(f"resolve({name!r}): gear/brand, no photo by design", file=sys.stderr)
            continue
        try:
            info = resolve_one(name)
        except Exception as ex:
            print(f"resolve({name!r}): {ex}", file=sys.stderr)
            continue
        if info:
            info["subject"] = name
            info["via"] = "wikipedia_article"
            return info
    return None


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
    ap.add_argument("--all", action="store_true", help="list every article image and why it was kept/rejected")
    a = ap.parse_args()
    if a.all:
        title, cands = candidates(a.subject)
        print(f"{a.subject} → {title or 'NO ARTICLE'}")
        for info, reason, lead in cands:
            print(f"  {'LEAD ' if lead else '     '}{'OK  ' if reason is None else 'no  '} {info['file']}  {info['orig_width']}x{info['orig_height']}  {reason or ''}")
        return
    info = resolve(a.subject, a.alt)
    if not info:
        sys.exit(f"no usable photo for {a.subject!r}")
    if a.out:
        fetch(info["url"], a.out)
        info["local"] = a.out
    print(json.dumps(info, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
