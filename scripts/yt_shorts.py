#!/usr/bin/env python3
"""Cross-post the latest published reel to YouTube Shorts.

Mac-side (launchd), because the upload rides the logged-in Dia session over
CDP (:9223) — the same recipe that shipped the amazon-review videos. Flow:

  newest queue/reels/<date>.json with "posted" but no "youtube"
    → gh run download <run_id> -n reel-<date>   (the mp4 GitHub Actions rendered)
    → upload via youtube.com/upload in Dia       (title = hook, #Shorts, Public)
    → write youtube.url back into the json, commit, push

  python3 scripts/yt_shorts.py [--date YYYY-MM-DD] [--dry-run]
Needs Dia launched with:  open -a Dia --args --remote-debugging-port=9223
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(ROOT, "queue", "reels")
BUILD = os.path.join(ROOT, "build")
CDP = "http://127.0.0.1:9223"


def sh(*cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=ROOT, **kw).stdout


def pending(date=None):
    files = sorted(f for f in os.listdir(QUEUE) if f.endswith(".json")) if os.path.isdir(QUEUE) else []
    for f in reversed(files):
        e = json.load(open(os.path.join(QUEUE, f)))
        if e.get("posted") and not e.get("youtube") and (date is None or e["date"] == date):
            return os.path.join(QUEUE, f), e
    return None, None


def fetch_render(e):
    out = os.path.join(BUILD, e["date"])
    mp4 = os.path.join(out, "reel.mp4")
    if not os.path.exists(mp4):
        os.makedirs(out, exist_ok=True)
        sh("gh", "run", "download", e["posted"]["run_id"], "-n", e["posted"]["artifact"], "-D", out)
    if not os.path.exists(mp4):
        sys.exit(f"artifact had no reel.mp4 in {out}")
    return mp4


def cdp_alive():
    try:
        urllib.request.urlopen(CDP + "/json/version", timeout=3)
        return True
    except OSError:
        return False


async def upload(video, title, desc):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp(CDP)
        page = await b.contexts[0].new_page()
        try:
            await page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector("ytcp-uploads-dialog input[type=file]", state="attached", timeout=45000)
            await page.set_input_files("ytcp-uploads-dialog input[type=file]", video)
            title_box = page.locator("#title-textarea #textbox")
            await title_box.wait_for(state="visible", timeout=60000)
            await page.wait_for_timeout(2000)
            await title_box.click()
            await page.keyboard.press("Meta+A")
            await page.keyboard.type(title, delay=10)
            desc_box = page.locator("#description-textarea #textbox")
            await desc_box.click()
            await page.keyboard.type(desc, delay=5)
            radio = page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']")
            await radio.scroll_into_view_if_needed()
            await radio.click()
            for _ in range(3):
                await page.locator("#next-button").click()
                await page.wait_for_timeout(1500)
            await page.locator("tp-yt-paper-radio-button[name='PUBLIC']").click()
            await page.wait_for_timeout(1000)
            await page.locator("#done-button").click()
            await page.wait_for_timeout(6000)
            link = None
            for _ in range(40):
                link = await page.evaluate(
                    "() => { const a = document.querySelector(\"a[href*='youtu.be'], ytcp-video-info a\"); return a ? a.href : null; }")
                if link:
                    break
                await page.wait_for_timeout(2000)
            try:
                close = page.locator("ytcp-button#close-button, #close-button")
                if await close.count():
                    await close.first.click()
            except Exception:  # noqa: BLE001
                pass
            return link
        finally:
            await page.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    sh("git", "pull", "--ff-only", "-q")
    path, e = pending(a.date)
    if not e:
        print("nothing to cross-post")
        return
    video = fetch_render(e)
    title = f"{e['hook']} #Shorts"[:100]
    desc = e["caption"].strip() + "\n\n" + " ".join(f"#{t}" for t in e.get("hashtags", [])[:10]) + "\n\n#Shorts"
    print(f"{e['date']}: {title}")
    if a.dry_run:
        print(video, len(desc), "chars")
        return
    if not cdp_alive():
        sys.exit("Dia CDP :9223 not reachable — launch: open -a Dia --args --remote-debugging-port=9223")
    link = asyncio.run(upload(video, title, desc))
    if not link:
        sys.exit("upload finished without a link — check YouTube Studio")
    e["youtube"] = {"url": link}
    json.dump(e, open(path, "w"), indent=1, ensure_ascii=False)
    sh("git", "add", os.path.relpath(path, ROOT))
    sh("git", "commit", "-q", "-m", f"reel: {e['date']} on shorts")
    sh("git", "push", "-q")
    print("LINK:", link)


if __name__ == "__main__":
    main()
