#!/usr/bin/env python3
"""Competitor research — the free stand-in for viralfindr.

Reads each competitor's public profile + last N posts through Instagram's own
web endpoints using the logged-in session in the running Dia browser (no 2nd
browser instance, no API keys). Writes:

  research/competitors.json   raw numbers per account/post
  research/competitors.md     digest the roadmap prompt consumes

Mac-only (needs Dia's cookie store). Run weekly, or before a roadmap refresh:

  /opt/homebrew/bin/python3 scripts/competitors.py                # handles from config.json
  /opt/homebrew/bin/python3 scripts/competitors.py a b c --probe  # just print stats, don't write
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_DB = os.path.expanduser("~/Library/Application Support/Dia/User Data/Default/Cookies")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
APP_ID = "936619743392459"
POSTS = 18


def dia_cookies(domain_like="%instagram.com"):
    pw = subprocess.check_output(["security", "find-generic-password", "-s", "Dia Safe Storage", "-w"]).strip()
    key = PBKDF2(pw, b"saltysalt", dkLen=16, count=1003)
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy(COOKIE_DB, tmp)
    con = sqlite3.connect(tmp)
    out = {}
    for host, name, blob in con.execute(
            "select host_key,name,encrypted_value from cookies where host_key like ?", (domain_like,)):
        if not blob.startswith(b"v10"):
            out[name] = blob.decode(errors="ignore")
            continue
        p = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(blob[3:])
        p = p[:-p[-1]]
        if len(p) > 32 and p[:32] == hashlib.sha256(host.encode()).digest():
            p = p[32:]
        out[name] = p.decode(errors="ignore")
    con.close()
    os.remove(tmp)
    return out


def _num(s):
    s = s.replace(",", "")
    mult = {"K": 1_000, "M": 1_000_000}.get(s[-1:], 1)
    return int(float(s.rstrip("KM")) * mult)


class IG:
    def __init__(self):
        ck = dia_cookies()
        if "sessionid" not in ck:
            sys.exit("no instagram sessionid in Dia — log into instagram.com in Dia first")
        keep = ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did")
        self.h = {
            "x-ig-app-id": APP_ID, "User-Agent": UA, "Referer": "https://www.instagram.com/",
            "x-csrftoken": ck.get("csrftoken", ""), "x-requested-with": "XMLHttpRequest",
            "Cookie": "; ".join(f"{k}={v}" for k, v in ck.items() if k in keep),
        }

    def get(self, url):
        req = urllib.request.Request(url, headers=self.h)
        return json.load(urllib.request.urlopen(req, timeout=30))

    def resolve(self, handle):
        """Handle → (id, profile dict). IG 400s randomly on the first endpoint, so fall through."""
        for url in (f"https://i.instagram.com/api/v1/users/web_profile_info/?username={handle}",
                    f"https://www.instagram.com/api/v1/users/web_profile_info/?username={handle}"):
            try:
                u = self.get(url)["data"]["user"]
                return u["id"], {
                    "full_name": u.get("full_name"), "follower_count": u["edge_followed_by"]["count"],
                    "media_count": u["edge_owner_to_timeline_media"]["count"],
                    "biography": u.get("biography"), "category": u.get("category_name"),
                }
            except (urllib.error.HTTPError, KeyError, TypeError):
                time.sleep(2)
        q = urllib.parse.quote(handle)
        d = self.get(f"https://www.instagram.com/api/v1/web/search/topsearch/?context=blended&query={q}")
        for r in d.get("users", []):
            u = r["user"]
            if u["username"].lower() == handle.lower():
                prof = {"full_name": u.get("full_name"), "follower_count": None,
                        "media_count": None, "biography": None, "category": None}
                prof.update(self.meta_counts(handle))
                return u["pk"], prof
        raise LookupError(handle)

    def meta_counts(self, handle):
        """Profile page <meta description> still says 'N Followers, N Following, N Posts'."""
        try:
            req = urllib.request.Request(f"https://www.instagram.com/{handle}/", headers={**self.h, "Accept": "text/html"})
            html = urllib.request.urlopen(req, timeout=30).read().decode(errors="ignore")
        except OSError:
            return {}
        m = re.search(r'content="([\d.,]+[KM]?) Followers, [\d.,]+[KM]? Following, ([\d.,]+[KM]?) Posts', html)
        if not m:
            return {}
        return {"follower_count": _num(m.group(1)), "media_count": _num(m.group(2))}

    def feed(self, uid, count=POSTS):
        d = self.get(f"https://www.instagram.com/api/v1/feed/user/{uid}/?count={count}")
        return d.get("items", [])


def shape(handle, prof, items):
    posts = []
    for it in items:
        t = {1: "photo", 2: "reel", 8: "carousel"}.get(it.get("media_type"), str(it.get("media_type")))
        cap = ((it.get("caption") or {}).get("text") or "").strip()
        posts.append({
            "code": it.get("code"), "type": t,
            "likes": it.get("like_count"), "comments": it.get("comment_count"),
            "plays": it.get("ig_play_count") or it.get("play_count"),
            "duration": round(it.get("video_duration") or 0, 1),
            "taken_at": datetime.fromtimestamp(it["taken_at"], timezone.utc).isoformat(timespec="minutes"),
            "hook": cap.split("\n")[0][:140], "caption": cap[:600],
        })
    stamps = sorted(datetime.fromisoformat(p["taken_at"]).timestamp() for p in posts)
    span_days = (stamps[-1] - stamps[0]) / 86400 if len(stamps) > 1 else 1
    reels = [p for p in posts if p["type"] == "reel"]
    plays = [p["plays"] for p in reels if p["plays"]]
    return {
        "username": handle, "full_name": prof.get("full_name"), "followers": prof.get("follower_count"),
        "media_count": prof.get("media_count"), "bio": (prof.get("biography") or "")[:300],
        "category": prof.get("category"), "posts_per_day": round(len(posts) / max(span_days, 1), 2),
        "reel_share": round(len(reels) / max(len(posts), 1), 2),
        "avg_reel_plays": int(sum(plays) / len(plays)) if plays else None,
        "avg_comments": int(sum(p["comments"] or 0 for p in posts) / max(len(posts), 1)),
        "posts": posts,
    }


def digest(accounts):
    out = ["# Competitor digest", "",
           f"_generated {datetime.now(timezone.utc).isoformat(timespec='minutes')} from Instagram public data_", ""]
    for a in accounts:
        out += [f"## @{a['username']} — {a['full_name']} ({a['followers'] or '?'} followers)",
                f"- category: {a['category']} · posts/day: {a['posts_per_day']} · reel share: {int(a['reel_share']*100)}% "
                f"· avg reel plays: {a['avg_reel_plays']} · avg comments: {a['avg_comments']}",
                f"- bio: {a['bio']}", "", "Top posts by engagement:"]
        ranked = sorted(a["posts"], key=lambda p: (p["plays"] or 0) + 20 * (p["comments"] or 0), reverse=True)[:6]
        for p in ranked:
            out.append(f"  - [{p['type']} {p['duration']}s] plays={p['plays']} comments={p['comments']} likes={p['likes']} "
                       f"— \"{p['hook']}\"")
        out.append("")
    return "\n".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    probe = "--probe" in sys.argv
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    handles = args or cfg["reels"]["competitors"]
    ig = IG()
    accounts = []
    for h in handles:
        try:
            uid, prof = ig.resolve(h)
            time.sleep(2)
            items = ig.feed(uid)
            a = shape(h, prof, items)
            accounts.append(a)
            print(f"@{h}: {a['followers'] or '?'} followers, {len(items)} posts, reel share {a['reel_share']}, "
                  f"avg plays {a['avg_reel_plays']}, {a['posts_per_day']}/day", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — one bad handle must not sink the run
            print(f"@{h}: FAILED {type(e).__name__} {str(e)[:120]}", file=sys.stderr)
        time.sleep(4)
    if probe or not accounts:
        print(json.dumps([{k: v for k, v in a.items() if k != "posts"} for a in accounts], indent=1))
        return
    os.makedirs(os.path.join(ROOT, "research"), exist_ok=True)
    json.dump(accounts, open(os.path.join(ROOT, "research", "competitors.json"), "w"), indent=1)
    open(os.path.join(ROOT, "research", "competitors.md"), "w").write(digest(accounts))
    print(f"wrote research/competitors.json + .md for {len(accounts)} accounts")


if __name__ == "__main__":
    main()
