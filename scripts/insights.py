#!/usr/bin/env python3
"""Read back how every posted reel actually performed (Instagram Graph insights via Composio).

  python3 scripts/insights.py            # refresh state/insights.json + print the table
  python3 scripts/insights.py --json     # machine output only

Per reel: views, reach, likes, comments, shares, saved, total_interactions, avg watch time.
Needs `composio login` (Mac) and CI=false (the CLI redacts ids under CI=true).
"""
import argparse
import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "state", "insights.json")
METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
           "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]


def composio(tool, data, account):
    env = {**os.environ, "CI": "false"}
    p = subprocess.run(["composio", "execute", tool, "--account", account, "-d", json.dumps(data)],
                       capture_output=True, text=True, timeout=120, env=env)
    out = p.stdout.strip()
    d = json.loads(out[out.index("{"):out.rindex("}") + 1])
    if not d.get("successful"):
        raise RuntimeError(json.dumps(d)[:400])
    return d["data"]


def flatten(data):
    """Graph insights come back as data[*].{name, values[0].value | total_value.value}."""
    items = data.get("data") if isinstance(data, dict) else None
    if isinstance(items, dict):
        items = items.get("data")
    row = {}
    for m in items or []:
        v = None
        if m.get("values"):
            v = m["values"][0].get("value")
        elif m.get("total_value"):
            v = m["total_value"].get("value")
        row[m["name"]] = v
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    acct = cfg["composio_account"]
    rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, "queue", "reels", "*.json"))):
        e = json.load(open(f))
        mid = (e.get("posted") or {}).get("media_id")
        if not mid:
            continue
        try:
            ins = flatten(composio("INSTAGRAM_GET_IG_MEDIA_INSIGHTS", {"ig_media_id": mid, "metric": METRICS}, acct))
        except Exception as ex:
            print(f"{e['date']}: insights failed: {ex}", file=sys.stderr)
            ins = {}
        rows.append({"date": e["date"], "series": e.get("series"), "hook": e.get("hook"), "media_id": mid,
                     "permalink": (e.get("posted") or {}).get("permalink"),
                     "photo": (e.get("photo") or {}).get("file"), "subject": e.get("subject"), **ins})
    json.dump(rows, open(OUT, "w"), indent=1, ensure_ascii=False)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    print(f"{'date':10} {'series':13} {'views':>5} {'reach':>5} {'like':>4} {'cmt':>3} {'shr':>3} {'sav':>3} {'avgs':>5}  hook")
    for r in sorted(rows, key=lambda r: -(r.get("views") or 0)):
        aw = r.get("ig_reels_avg_watch_time")
        aw = f"{aw/1000:.1f}" if isinstance(aw, (int, float)) else "-"
        print(f"{r['date']:10} {(r['series'] or '')[:13]:13} {r.get('views') or 0:5} {r.get('reach') or 0:5} "
              f"{r.get('likes') or 0:4} {r.get('comments') or 0:3} {r.get('shares') or 0:3} {r.get('saved') or 0:3} {aw:>5}  {(r['hook'] or '')[:48]}")


if __name__ == "__main__":
    main()
