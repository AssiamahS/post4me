#!/usr/bin/env python3
"""Publish a rendered reel to Instagram through Composio (the later.com step).

  python3 scripts/publish_reel.py build/2026-08-25/reel.mp4 entry.json

Prints one JSON line: {"media_id": ..., "permalink": ...}. Exits non-zero if the
API did not confirm success — never trust a 200 alone.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def composio(tool, data, account):
    p = subprocess.run(["composio", "execute", tool, "--account", account, "-d", json.dumps(data)],
                       capture_output=True, text=True, timeout=900)
    out = p.stdout.strip()
    try:
        d = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        sys.exit(f"{tool}: unparseable output\n{out[-800:]}\n{p.stderr[-400:]}")
    if not d.get("successful"):
        sys.exit(f"{tool}: not successful\n{json.dumps(d)[:1200]}")
    return d["data"]


def build_caption(e):
    tags = " ".join(f"#{t}" for t in e.get("hashtags", []))
    text = e["caption"].strip() + ("\n.\n.\n" + tags if tags else "")
    return text


def main():
    video, entry_path = sys.argv[1], sys.argv[2]
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    e = json.load(open(entry_path))
    ig, acct = cfg["ig_user_id"], cfg["composio_account"]
    caption = build_caption(e)

    container = composio("INSTAGRAM_POST_IG_USER_MEDIA", {
        "ig_user_id": ig, "media_type": "REELS", "video_file": os.path.abspath(video),
        "caption": caption, "share_to_feed": True, "thumb_offset": 0,
        "audio_name": cfg.get("reels", {}).get("audio_name", "Original Audio"),
    }, acct)
    cid = container["id"]
    print(f"container {cid}", file=sys.stderr)

    # tool caps max_wait_seconds at 300; reels can take longer to process, so give it 3 windows
    published = None
    for attempt in range(3):
        try:
            published = composio("INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH",
                                 {"ig_user_id": ig, "creation_id": cid, "max_wait_seconds": 300}, acct)
            break
        except SystemExit as ex:
            print(f"publish attempt {attempt + 1} failed: {str(ex)[:300]}", file=sys.stderr)
            if attempt == 2:
                raise
    mid = published["id"]

    permalink = None
    try:
        media = composio("INSTAGRAM_GET_IG_MEDIA", {"ig_media_id": mid, "fields": "permalink,media_type,timestamp"}, acct)
        permalink = media.get("permalink") or (media.get("data") or {}).get("permalink")
    except SystemExit:
        pass
    print(json.dumps({"media_id": mid, "permalink": permalink, "caption_len": len(caption)}))


if __name__ == "__main__":
    main()
