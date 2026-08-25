#!/usr/bin/env python3
"""Pick today's roadmap entry and stage it.

Walks roadmap/month-NN.json sequentially using state/reel_cursor.json — a
missed day just shifts the calendar, nothing is skipped. Idempotent per date:
if queue/reels/<date>.json already exists it is returned unchanged.

  python3 scripts/next_reel.py [--date YYYY-MM-DD] [--peek]
Prints the staged entry path + entry JSON on one line.
"""
import argparse
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURSOR = os.path.join(ROOT, "state", "reel_cursor.json")
QUEUE = os.path.join(ROOT, "queue", "reels")


def load_month(n):
    p = os.path.join(ROOT, "roadmap", f"month-{n:02d}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--peek", action="store_true", help="don't advance the cursor or write files")
    a = ap.parse_args()

    staged = os.path.join(QUEUE, f"{a.date}.json")
    if os.path.exists(staged):
        e = json.load(open(staged))
        print(json.dumps({"path": os.path.relpath(staged, ROOT), "entry": e, "reused": True}))
        return

    cur = json.load(open(CURSOR)) if os.path.exists(CURSOR) else {"month": 1, "day": 1}
    month = load_month(cur["month"])
    if month is None:
        sys.exit(f"roadmap/month-{cur['month']:02d}.json missing — run scripts/roadmap.py --month {cur['month']}")
    if cur["day"] > len(month):
        cur = {"month": cur["month"] + 1, "day": 1}
        month = load_month(cur["month"])
        if month is None:
            sys.exit(f"roadmap exhausted at month {cur['month']} — generate the next month")
    e = dict(month[cur["day"] - 1])
    e["date"] = a.date
    e["cursor"] = dict(cur)

    if not a.peek:
        os.makedirs(QUEUE, exist_ok=True)
        os.makedirs(os.path.dirname(CURSOR), exist_ok=True)
        json.dump(e, open(staged, "w"), indent=1, ensure_ascii=False)
        json.dump({"month": cur["month"], "day": cur["day"] + 1}, open(CURSOR, "w"))
    print(json.dumps({"path": os.path.relpath(staged, ROOT), "entry": e, "reused": False}))


if __name__ == "__main__":
    main()
