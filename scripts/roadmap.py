#!/usr/bin/env python3
"""The Claude step: competitor digest → 6-month strategy → 30 reel scripts per month.

Runs on the Mac through the `claude` CLI (subscription, no API key). Output is
committed to the repo, so GitHub Actions only ever renders + posts — it never
needs to call a model.

  python3 scripts/roadmap.py --strategy          # roadmap/STRATEGY.md
  python3 scripts/roadmap.py --month 1           # roadmap/month-01.json (30 entries)
  python3 scripts/roadmap.py --all               # strategy + months 1..6
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RD = os.path.join(ROOT, "roadmap")
DAYS = 30
MONTHS = 6

ENTRY_SPEC = """Each entry is an object with EXACTLY these keys:
- "day": integer 1-%d
- "series": one of the series names from the strategy (short, uppercase-able)
- "title": internal working title, <= 8 words
- "hook": the first spoken line AND the cover text — <= 9 words, stops the scroll, no clickbait lies
- "voiceover": the full spoken script, 85-115 words, starts with the hook verbatim, plain conversational
  English, short sentences, one concrete story/fact/lesson, ends with a soft CTA that fits the series.
  No emojis, no hashtags, no stage directions, no "in this video". Numbers as digits.
- "caption": Instagram caption 40-90 words that adds context or a question; may use 1-2 emojis
- "hashtags": array of 12-18 hashtags without the # sign, mixed niche + broad
- "cover_text": 3-6 words for the cover card (can equal the hook if short)
""" % DAYS


def claude(prompt, max_retries=2):
    for attempt in range(max_retries + 1):
        p = subprocess.run(["claude", "-p", prompt, "--output-format", "text"],
                           capture_output=True, text=True, timeout=1800)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
        print(f"claude attempt {attempt} failed: {p.stderr[-300:]}", file=sys.stderr)
    sys.exit("claude -p failed")


def read(rel):
    p = os.path.join(ROOT, rel)
    return open(p).read() if os.path.exists(p) else ""


def cfg():
    return json.load(open(os.path.join(ROOT, "config.json")))


def strategy():
    c = cfg()
    r = c["reels"]
    digest = read("research/competitors.md") or "(no competitor research yet — reason from the niche alone)"
    prompt = f"""You are a short-form content strategist. Build a 6-month Instagram Reels roadmap for this account.

ACCOUNT
- handle: @{c['handle']}
- niche: {r['niche']}
- who runs it: {r.get('about', 'a working DJ')}
- existing daily series already running as stories: "{c.get('series_tag')}" (90s/00s rap quotes)
- goal: grow a niche audience that brands in this space will pay to reach (gear, software, events, apparel),
  while staying authentic — the person behind it is a real DJ, not a content farm.
- one reel per day, faceless, voiceover + kinetic captions on a dark card. No licensed music (original audio only).

COMPETITOR RESEARCH (public data, last ~18 posts each)
{digest}

Write roadmap/STRATEGY.md in markdown with these sections:
1. Positioning — one paragraph: what this account is the go-to for, and how it differs from each competitor above.
2. Content pillars — exactly 5 recurring SERIES. For each: NAME (2-3 words, works as an on-screen tag),
   promise to the viewer, hook formula, example hook, ideal length, CTA style.
3. Weekly cadence — which series posts on which weekday (7 days, all 5 series covered, 2 get two slots).
4. Hook rules — 8 rules derived from what performs in the research (quote the patterns you see).
5. Six-month arc — month by month (1-6): theme, what shifts, what milestone to aim for, what to test.
6. Monetization path — when and how to approach brands, what a media kit needs, which categories fit.
7. Guardrails — accuracy (no invented quotes/dates), no copyrighted audio, no drama-baiting, credit sources.

Be specific to hip-hop / DJ culture. Output ONLY the markdown document, no preamble."""
    out = claude(prompt)
    os.makedirs(RD, exist_ok=True)
    open(os.path.join(RD, "STRATEGY.md"), "w").write(out.strip() + "\n")
    print("wrote roadmap/STRATEGY.md")


def used_titles():
    seen = []
    for f in sorted(os.listdir(RD)) if os.path.isdir(RD) else []:
        if f.startswith("month-") and f.endswith(".json"):
            for e in json.load(open(os.path.join(RD, f))):
                seen.append(f"{e.get('series')}: {e.get('title')}")
    return seen


def parse_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("["), text.rfind("]")
    return json.loads(text[start:end + 1])


def month(n):
    c = cfg()
    r = c["reels"]
    strat = read("roadmap/STRATEGY.md")
    if not strat:
        sys.exit("run --strategy first")
    digest = read("research/competitors.md")
    prior = used_titles()
    prior_txt = "\n".join(f"- {t}" for t in prior[-200:]) or "- (none yet)"
    prompt = f"""You are writing month {n} of 6 of a daily Instagram Reels roadmap. Follow the strategy exactly.

STRATEGY
{strat}

COMPETITOR RESEARCH (for hook style and what performs)
{digest[:6000]}

ALREADY USED TOPICS (do not repeat or paraphrase any of these)
{prior_txt}

TASK
Write {DAYS} reel entries for month {n}. Day 1 is a Monday; follow the weekly cadence from the strategy so
each weekday gets its assigned series. Apply the month-{n} theme from the six-month arc.
Every fact, quote, date, sample, or chart position must be real and verifiable — if unsure, pick a
different, well-documented story. Mix eras (80s-2020s) and regions. Vary hook formulas day to day.
Voice: {r.get('about', 'a working DJ')} talking to other DJs and heads — knowledgeable, warm, zero hype.

{ENTRY_SPEC}
Output ONLY a JSON array of {DAYS} objects. No markdown fences, no commentary."""
    for attempt in range(2):
        out = claude(prompt)
        try:
            entries = parse_json(out)
            bad = [e for e in entries if not all(k in e for k in ("day", "series", "title", "hook", "voiceover", "caption", "hashtags"))]
            if len(entries) < DAYS - 2 or bad:
                raise ValueError(f"{len(entries)} entries, {len(bad)} malformed")
            break
        except (ValueError, json.JSONDecodeError) as ex:
            print(f"month {n} attempt {attempt}: {ex}", file=sys.stderr)
            entries = None
    if not entries:
        sys.exit(f"month {n} failed")
    for i, e in enumerate(entries):
        e["day"] = i + 1
        e["month"] = n
        e["hashtags"] = [h.lstrip("#").strip() for h in e["hashtags"]][:20]
    json.dump(entries, open(os.path.join(RD, f"month-{n:02d}.json"), "w"), indent=1, ensure_ascii=False)
    words = [len(e["voiceover"].split()) for e in entries]
    print(f"wrote roadmap/month-{n:02d}.json: {len(entries)} entries, voiceover {min(words)}-{max(words)} words")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", action="store_true")
    ap.add_argument("--month", type=int)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.all:
        strategy()
        for n in range(1, MONTHS + 1):
            month(n)
    elif a.strategy:
        strategy()
    elif a.month:
        month(a.month)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
