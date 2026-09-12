#!/usr/bin/env python3
"""Tag every roadmap entry with WHO it is about, then pin a licensed photo to it.

Mac-side, like roadmap.py: one `claude -p` call per month names the subject
(an English Wikipedia article title) + an alternate, then subject_image.py
resolves each to a Wikimedia Commons photo and the result is written into the
entry as "photo". GitHub Actions then only downloads — no model, no search.

  python3 scripts/subjects.py                 # every roadmap/month-NN.json missing subjects
  python3 scripts/subjects.py --month 2       # one month
  python3 scripts/subjects.py --file queue/reels/2026-09-13.json   # one staged entry
  python3 scripts/subjects.py --resolve-only  # skip claude, just (re)fetch photos for tagged entries
  python3 scripts/subjects.py --force         # re-tag entries that already have a subject
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subject_image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RD = os.path.join(ROOT, "roadmap")
KEEP = ("file", "url", "page", "license", "artist", "credit", "subject", "via", "orig_width", "orig_height")

PROMPT = """For each reel below, name WHO should be in the photo behind the captions.

Rules:
- "subject": the exact English Wikipedia article title of the person or group whose face best sells
  the story — the name a scroller recognises. Use Wikipedia's own disambiguated form when needed
  (e.g. "Common (rapper)", "Nas", "Pete Rock", "The Notorious B.I.G.").
- "subject_alt": a second choice, also a Wikipedia title — usually the other party in the story
  (the sampled artist, the drummer, the producer). Never the same as subject.
- SAMPLE SOURCE: subject = the rapper/producer who flipped it, alt = the original artist.
- THE BREAK: subject = the drummer or band that cut the break, alt = the most famous act that used it.
- ON THIS DAY / CRATE NOTES: subject = the artist of the record, alt = a producer/rapper tied to it.
- DJ CRAFT (technique tips, no named artist): subject = a well-photographed hip-hop DJ or piece of
  gear that fits the tip (e.g. "Grandmaster Flash", "DJ Premier", "Jazzy Jeff", "Technics SL-1200",
  "Serato"), alt = another. Use null only if nothing fits at all.
- Prefer subjects likely to have a real photo on Wikipedia (well-known, living or well-documented).
- Do not invent names. If the reel's facts name a person, use that person.

REELS
%s

Output ONLY a JSON array of objects {"idx": int, "subject": str|null, "subject_alt": str|null},
one per reel, same idx values, no markdown fences, no commentary."""


def claude(prompt):
    for attempt in range(3):
        try:
            p = subprocess.run(["claude", "-p", prompt, "--output-format", "text"],
                               capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            print(f"claude attempt {attempt} timed out", file=sys.stderr)
            continue
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
        print(f"claude attempt {attempt} failed: {p.stderr[-300:]}", file=sys.stderr)
    sys.exit("claude -p failed")


def parse_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text[text.find("["):text.rfind("]") + 1])


def tag(entries, force=False):
    """Ask claude for subject/subject_alt on entries that lack one. Mutates in place."""
    todo = [(i, e) for i, e in enumerate(entries) if force or not e.get("subject")]
    if not todo:
        return 0
    lines = []
    for i, e in todo:
        vo = " ".join(e.get("voiceover", "").split()[:30])
        lines.append(f"{i} | {e.get('series')} | {e.get('title')} | {e.get('hook')} | {vo}")
    out = parse_json(claude(PROMPT % "\n".join(lines)))
    by_idx = {int(o["idx"]): o for o in out if "idx" in o}
    n = 0
    for i, e in todo:
        o = by_idx.get(i)
        if not o:
            print(f"  no subject returned for idx {i}: {e.get('title')}", file=sys.stderr)
            continue
        e["subject"] = (o.get("subject") or None)
        e["subject_alt"] = (o.get("subject_alt") or None)
        e.pop("photo", None)  # subject changed → photo must be re-resolved
        n += 1
    return n


def resolve(entries, force=False):
    """Pin a Commons photo to every entry with a subject. Mutates in place; returns (found, missing)."""
    found = missing = 0
    for e in entries:
        if not e.get("subject") or (e.get("photo") and not force):
            continue
        info = subject_image.resolve(e["subject"], e.get("subject_alt"))
        if info:
            e["photo"] = {k: info[k] for k in KEEP if k in info}
            found += 1
            print(f"  {e.get('title')!s:45} → {info['subject']} ({info['orig_width']}x{info['orig_height']}, {info['license']})")
        else:
            e.pop("photo", None)
            missing += 1
            print(f"  {e.get('title')!s:45} → NO PHOTO for {e['subject']!r} / {e.get('subject_alt')!r}")
    return found, missing


def process(path, a):
    data = json.load(open(path))
    entries = data if isinstance(data, list) else [data]
    print(f"{os.path.relpath(path, ROOT)}: {len(entries)} entries")
    if not a.resolve_only:
        n = tag(entries, force=a.force)
        print(f"  tagged {n}")
    found, missing = resolve(entries, force=a.force)
    print(f"  photos: {found} found, {missing} missing")
    json.dump(data, open(path, "w"), indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", type=int)
    ap.add_argument("--file")
    ap.add_argument("--resolve-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.file:
        paths = [os.path.join(ROOT, a.file)]
    elif a.month:
        paths = [os.path.join(RD, f"month-{a.month:02d}.json")]
    else:
        paths = sorted(os.path.join(RD, f) for f in os.listdir(RD) if re.match(r"month-\d\d\.json$", f))
    for p in paths:
        if not os.path.exists(p):
            sys.exit(f"missing {p}")
        process(p, a)


if __name__ == "__main__":
    main()
