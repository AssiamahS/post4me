#!/usr/bin/env python3
"""The sample-reel lane, Mac side (launchd com.sly.post4me-samples, 11:30 local).

  next unrendered roadmap/samples/*.json  → scripts/sample_reel.py (yt-dlp + ffmpeg, so it runs
  here, not on GitHub: YouTube blocks Actions runners)
  → iMessage: cover.jpg, the reel.mp4 itself (watch it before you answer), then the text:
        SAMPLE REEL <date> · <title>
        1. song → flip (match 0.71, original @12.4s, flip @0.0s)
        ...
        Reply YES to post or NO to skip.
  → registers the draft in ~/.post4me/approvals.json with a "local" block; approval_texter.py
    turns your YES into publish_reel.py right here and texts the permalink back.

  python3 scripts/sample_daily.py               # render + text the next one
  python3 scripts/sample_daily.py --slug nas-illmatic --date 2026-09-20
  python3 scripts/sample_daily.py --render-only --slug dre-funk   # no text, just the files
One draft per calendar day: if today's key already exists in approvals.json, nothing happens.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import approval_texter as at  # noqa: E402

VENV_PY = os.path.expanduser("~/.yt-dlp-venv/bin/python")
SAMPLES = os.path.join(ROOT, "roadmap", "samples")


def next_entry(slug=None):
    for p in sorted(glob.glob(os.path.join(SAMPLES, "*.json"))):
        e = json.load(open(p))
        if slug and e.get("slug") != slug:
            continue
        if not slug and (e.get("posted") or e.get("skipped") or e.get("drafted")):
            continue
        return p, e
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--render-only", action="store_true")
    a = ap.parse_args()
    key = f"s:{a.date}"
    st = at.load_state()
    if key in st and not a.render_only:
        print(f"{key} already drafted ({st[key].get('decision') or 'pending'})")
        return
    path, e = next_entry(a.slug)
    if not e:
        sys.exit("no sample reel left to draft — add one to roadmap/samples/")
    out = os.path.join(ROOT, "build", "samples", e["slug"])
    print(f"rendering {e['slug']} → {out}", flush=True)
    r = subprocess.run([VENV_PY, os.path.join(ROOT, "scripts", "sample_reel.py"), path, out],
                       capture_output=True, text=True, timeout=1500)
    if r.returncode != 0:
        sys.exit(f"render failed:\n{r.stderr[-1500:]}")
    meta = json.loads(r.stdout.strip().splitlines()[-1])
    e = json.load(open(path))  # sample_reel pinned yt ids + starts into it
    if a.render_only:
        print(json.dumps(meta, indent=1))
        return
    e["drafted"] = a.date
    e["date"] = a.date
    json.dump(e, open(path, "w"), indent=1, ensure_ascii=False)
    # the phone gets the video and one line — the match notes live in render.json / the log
    text = f"{e['title']} · {a.date} · {meta['duration']:.0f}s — reply YES or NO on the video"
    print("\n".join(meta["notes"]))
    before = at.last_rowid()
    at.imessage(file=meta["reel"])   # the video is the whole prompt; reply YES/NO on it
    time.sleep(3)   # Messages writes the rows a beat after the AppleScript returns
    st = at.load_state()
    st[key] = {"date": a.date, "hook": e["title"], "prompt_rowid": before, "sent": time.time(),
               "prompt_guid": at.sent_guid(before, "SAMPLE REEL"), "prompt_guids": at.sent_guids(before),
               "decision": None, "outcome": None,
               "local": {"entry": path, "mp4": meta["reel"], "slug": e["slug"]}}
    at.save_state(st)
    print(f"texted {key}: {e['title']}")


if __name__ == "__main__":
    main()
