#!/usr/bin/env python3
"""Rewrite one staged reel from Sly's feedback, then hand it back to the daily-reel workflow.

Mac-side (`claude -p`, no API key). Called by approval_texter.py when a NO reply carries
text, or by hand:

  python3 scripts/revise_reel.py queue/reels/2026-09-15.json "less words, lead with the year"
  python3 scripts/revise_reel.py queue/reels/2026-09-15.json "..." --no-dispatch   # rewrite only

What it does:
  1. appends the feedback to roadmap/FEEDBACK.md (standing notes every future script obeys)
  2. asks claude for a new hook / voiceover / caption / cover_text (and subject if the
     feedback asks for a different person), obeying STRATEGY.md + FEEDBACK.md + this note
  3. re-resolves the subject photo if the subject changed, clears skipped/posted, records
     the revision on the entry, commits + pushes
  4. dispatches daily-reel.yml for that date → new render → new reel-draft issue → new text
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subject_image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK = os.path.join(ROOT, "roadmap", "FEEDBACK.md")
STRATEGY = os.path.join(ROOT, "roadmap", "STRATEGY.md")
KEEP = ("file", "url", "page", "license", "artist", "credit", "subject", "via", "orig_width", "orig_height")

PROMPT = """You write the daily Instagram reel for @sl.ysl.yy, a working hip-hop / amapiano / jersey club DJ.
The reel is a faceless card: one photo of the subject, big 3-word caption cards, a voice reading the
script. Original audio only, no licensed music.

Sly rejected today's draft. His feedback on THIS draft:
>>> %(feedback)s

Standing feedback he has given before (all of it still applies):
%(standing)s

Series rules (from the strategy doc):
%(strategy)s

The rejected draft:
%(entry)s

Rewrite it. Keep the same series, the same underlying story/facts and the same date. Change what the
feedback asks for. If the feedback says the words are too many or the tone is tacky, the voiceover
must be 45-70 words, every sentence under 12 words, no narrator filler, no moral at the end, no
"save this". If the feedback names a different person or angle, follow it and set "subject" to that
person's exact English Wikipedia article title.

Output ONLY a JSON object with these keys: "hook" (<= 9 words, first spoken line and cover text),
"voiceover" (starts with the hook verbatim), "caption" (<= 60 words, one question max),
"cover_text" (3-6 words), "subject" (Wikipedia title or null), "subject_alt" (Wikipedia title or null),
"what_changed" (one sentence for Sly). No markdown fences."""


def claude(prompt):
    for attempt in range(3):
        try:
            p = subprocess.run(["claude", "-p", prompt, "--output-format", "text"],
                               capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            print(f"claude attempt {attempt} timed out", file=sys.stderr)
            continue
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
        print(f"claude attempt {attempt} failed: {p.stderr[-300:]}", file=sys.stderr)
    sys.exit("claude -p failed")


def parse_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text[text.find("{"):text.rfind("}") + 1])


def series_rules(series):
    """The strategy paragraph for this series, so the rewrite keeps its promise/CTA style."""
    txt = open(STRATEGY).read() if os.path.exists(STRATEGY) else ""
    m = re.search(rf"### {re.escape(series)}.*?(?=\n### |\n---|\Z)", txt, re.S | re.I)
    return (m.group(0) if m else txt[:1500]).strip()


def remember(feedback, date):
    with open(FEEDBACK, "a") as fh:
        fh.write(f"- {dt.date.today().isoformat()} — on the {date} draft: {feedback.strip()}\n")


def sh(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=ROOT).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entry")
    ap.add_argument("feedback")
    ap.add_argument("--no-dispatch", action="store_true", help="rewrite + commit only")
    ap.add_argument("--no-git", action="store_true", help="rewrite the file only")
    a = ap.parse_args()

    path = os.path.join(ROOT, a.entry)
    e = json.load(open(path))
    date = e.get("date") or os.path.basename(path)[:-5]
    remember(a.feedback, date)
    standing = open(FEEDBACK).read()
    shown = {k: e.get(k) for k in ("series", "title", "hook", "voiceover", "caption", "cover_text", "subject", "subject_alt")}
    out = parse_json(claude(PROMPT % {
        "feedback": a.feedback, "standing": standing, "strategy": series_rules(e.get("series", "")),
        "entry": json.dumps(shown, indent=1, ensure_ascii=False)}))

    e.setdefault("revisions", []).append({
        "at": dt.datetime.now().isoformat(timespec="seconds"), "feedback": a.feedback,
        "before": {k: e.get(k) for k in ("hook", "voiceover", "caption", "cover_text", "subject")},
        "what_changed": out.get("what_changed")})
    for k in ("hook", "voiceover", "caption", "cover_text"):
        if out.get(k):
            e[k] = out[k].strip()
    new_subject = out.get("subject") or e.get("subject")
    if new_subject != e.get("subject") or out.get("subject_alt") != e.get("subject_alt"):
        e["subject"], e["subject_alt"] = new_subject, out.get("subject_alt") or None
        e.pop("photo", None)
    if (e.get("photo") or {}).get("via") != "wikipedia_article":
        e.pop("photo", None)  # pinned by the old Commons-search picker — never carry it forward
    if e.get("subject") and not e.get("photo"):
        info = subject_image.resolve(e["subject"], e.get("subject_alt"))
        if info:
            e["photo"] = {k: info[k] for k in KEEP if k in info}
    for k in ("skipped", "posted", "deleted_by_user", "deleted_reason"):
        e.pop(k, None)
    json.dump(e, open(path, "w"), indent=1, ensure_ascii=False)
    words = len(e["voiceover"].split())
    print(json.dumps({"date": date, "hook": e["hook"], "words": words, "subject": e.get("subject"),
                      "photo": (e.get("photo") or {}).get("file"), "what_changed": out.get("what_changed")},
                     ensure_ascii=False))

    if a.no_git:
        return
    sh("git", "add", os.path.relpath(path, ROOT), "roadmap/FEEDBACK.md")
    sh("git", "commit", "-q", "-m", f"reel: revise {date} — {a.feedback.strip()[:60]}")
    sh("git", "pull", "-q", "--rebase", "origin", "main")
    sh("git", "push", "-q", "--no-verify", "origin", "main")
    if not a.no_dispatch:
        sh("gh", "workflow", "run", "daily-reel.yml", "-f", f"date={date}")
        print(f"dispatched daily-reel.yml for {date}", file=sys.stderr)


if __name__ == "__main__":
    main()
