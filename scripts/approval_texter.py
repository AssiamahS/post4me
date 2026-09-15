#!/usr/bin/env python3
"""Text every reel draft to the phone and turn the YES / NO reply into the GitHub decision.

Mac-side (launchd com.sly.post4me-approve, every 5 min). GitHub Actions stages the
reel and opens a `reel-draft` issue (config.json reels.post_mode = "approve");
nothing posts until an OWNER comment says yes. This script is the phone leg:

  open reel-draft issue, not yet texted
      → iMessage to my own iCloud thread: cover.jpg + hook + subject/photo + "reply YES or NO"
  reply YES / NO in that thread (read from ~/Library/Messages/chat.db)
      → `gh issue comment <n> --body yes|no`   (OWNER comment → post-approved.yml posts or skips)
  issue closed by the workflow
      → text back the permalink (or "skipped")

  python3 scripts/approval_texter.py            # one pass (what launchd runs)
  python3 scripts/approval_texter.py --dry-run  # show what it would do, send nothing
  python3 scripts/approval_texter.py --status   # pending prompts + their state

Reply forms: "yes" / "no" alone = the oldest pending draft; "yes 2026-09-15" = that date.
Anything after a "no" — or any reply that is not a yes/no at all — is FEEDBACK: the draft is
rejected, scripts/revise_reel.py rewrites it from the feedback (claude -p), the workflow
re-renders it and a new draft lands on the phone. Feedback is also kept in roadmap/FEEDBACK.md
so every future script obeys it.
Needs Full Disk Access for the python running it (chat.db) and Automation → Messages.
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

REPO = "AssiamahS/post4me"
ME = "sly.assiamah@icloud.com"          # my own iMessage thread = a text on my phone
CHAT_DB = os.path.expanduser("~/Library/Messages/chat.db")
STATE_DIR = os.path.expanduser("~/.post4me")
STATE = os.path.join(STATE_DIR, "approvals.json")
YES = re.compile(r"^\s*(yes|y|post|ship|approve|approved|go)\s*(20\d\d-\d\d-\d\d)?\s*[.!]?\s*$", re.I)
NO = re.compile(r"^\s*(no|n|skip|reject|nah|kill)\b[\s.,:!-]*(20\d\d-\d\d-\d\d)?[\s.,:!-]*(?P<fb>.*)$", re.I | re.S)
NOISE = re.compile(r"^\s*(ok|okay|k|thanks|thx|lol|👍|👌|🔥)\s*[.!]?\s*$", re.I)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATE = re.compile(r"(20\d\d-\d\d-\d\d)")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def sh(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def load_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


# ---------- iMessage ----------

def applescript(script):
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True, timeout=60)


def imessage(text=None, file=None):
    """Send text and/or a file to my own thread."""
    head = 'tell application "Messages"\nset svc to 1st account whose service type = iMessage\n'
    tail = "\nend tell"
    if file:
        applescript(head + f'send POSIX file "{file}" to buddy "{ME}" of svc' + tail)
    if text:
        safe = text.replace("\\", "\\\\").replace('"', '\\"')
        applescript(head + f'send "{safe}" to buddy "{ME}" of svc' + tail)


def _decode_body(blob):
    """chat.db keeps modern message text in attributedBody (typedstream), text column NULL."""
    if not blob:
        return ""
    i = blob.find(b"NSString")
    if i < 0:
        return ""
    i = blob.find(b"+", i)
    if i < 0:
        return ""
    i += 1
    n = blob[i]
    if n == 0x81:
        n = int.from_bytes(blob[i + 1:i + 3], "little")
        i += 3
    else:
        i += 1
    return blob[i:i + n].decode("utf-8", "replace")


def thread_messages(after_rowid):
    """[(rowid, text)] in my own thread newer than after_rowid."""
    db = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    rows = db.execute(
        """SELECT m.ROWID, m.text, m.attributedBody FROM message m
           JOIN chat_message_join j ON j.message_id = m.ROWID
           JOIN chat c ON c.ROWID = j.chat_id
           WHERE c.chat_identifier = ? AND m.ROWID > ? ORDER BY m.ROWID""", (ME, after_rowid)).fetchall()
    db.close()
    return [(r[0], (r[1] or _decode_body(r[2])).strip()) for r in rows]


def last_rowid():
    db = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    r = db.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0]
    db.close()
    return r


# ---------- GitHub ----------

def open_drafts():
    out = sh("gh", "issue", "list", "-R", REPO, "--label", "reel-draft", "--state", "open",
             "--json", "number,title,body,createdAt", "--limit", "20")
    return sorted(json.loads(out), key=lambda i: i["number"])


def issue_outcome(number):
    """('posted', permalink) / ('skipped', None) / (None, None) once the workflow has acted."""
    d = json.loads(sh("gh", "issue", "view", str(number), "-R", REPO, "--json", "state,comments"))
    for c in d.get("comments", []):
        body = c.get("body", "")
        if body.startswith("posted"):
            m = re.search(r"https?://\S+", body)
            return "posted", (m.group(0) if m else None)
        if body.startswith("skipped"):
            return "skipped", None
    return None, None


def parse_issue(issue):
    body = issue["body"]
    m = re.search(r"\*\*(.+?)\*\* — (.+)", body)
    series, hook = (m.group(1), m.group(2)) if m else ("", issue["title"])
    cover = (re.search(r"!\[cover\]\((\S+)\)", body) or [None, None])[1]
    photo = (re.search(r"\*\*Photo\*\*: (.+)", body) or [None, "photo: ?"])[1]
    file = (re.search(r"file: (\S+)", body) or [None, ""])[1]
    date = (DATE.search(file) or DATE.search(issue["title"]) or [None, "?"])[1]
    return {"series": series, "hook": hook, "cover": cover, "photo": photo, "date": date}


def fetch(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "post4me-approve/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as fh:
        fh.write(r.read())
    return dest


# ---------- the pass ----------

def prompt_new(issues, st, dry):
    for it in issues:
        key = str(it["number"])
        if key in st:
            continue
        info = parse_issue(it)
        text = (f"REEL DRAFT {info['date']} · {info['series']}\n{info['hook']}\n"
                f"{info['photo'][:160]}\n"
                f"Reply YES to post or NO to skip. (github issue #{it['number']})")
        log(f"prompt #{it['number']} {info['date']}: {info['hook'][:60]}")
        if dry:
            continue
        cover = None
        if info["cover"]:
            try:
                cover = fetch(info["cover"], os.path.join(STATE_DIR, "covers", f"{info['date']}.jpg"))
            except Exception as ex:
                log(f"  cover download failed: {ex}")
        before = last_rowid()
        imessage(file=cover)
        imessage(text=text)
        st[key] = {"date": info["date"], "hook": info["hook"], "prompt_rowid": before, "sent": time.time(),
                   "decision": None, "outcome": None}
        save_state(st)


def read_replies(st, dry):
    pending = sorted((k for k, v in st.items() if v.get("decision") is None), key=lambda k: st[k]["date"])
    if not pending:
        return
    floor = min(st[k]["prompt_rowid"] for k in pending)
    for rowid, text in thread_messages(floor):
        if not text or NOISE.match(text) or text.startswith("REEL DRAFT") or text.startswith("post4me"):
            continue
        if YES.match(text):
            verdict, feedback = "yes", ""
        elif NO.match(text):
            verdict, feedback = "no", NO.match(text).group("fb").strip()
        else:
            verdict, feedback = "no", text.strip()   # a plain sentence = "not this, do it like X"
        m = DATE.search(text)
        targets = [k for k in pending if st[k]["date"] == m.group(1)] if m else pending[:1]
        for k in targets:
            if rowid <= st[k]["prompt_rowid"] or st[k]["decision"]:
                continue
            log(f"reply {verdict!r} (msg {rowid}) → issue #{k} {st[k]['date']}" + (f" feedback: {feedback[:80]}" if feedback else ""))
            if dry:
                continue
            sh("gh", "issue", "comment", k, "-R", REPO, "--body", verdict + (f"\n\nfeedback: {feedback}" if feedback else ""))
            st[k]["decision"] = verdict
            st[k]["decided_rowid"] = rowid
            st[k]["feedback"] = feedback or None
            st[k]["revise"] = "pending" if feedback else None
            save_state(st)
            if verdict == "yes":
                imessage(text=f"Posting {st[k]['date']}. I will text the link when Instagram confirms.")
            elif feedback:
                imessage(text=f"Got it. Rewriting {st[k]['date']} with: \"{feedback[:200]}\". New draft here in ~5 min.")
            else:
                imessage(text=f"Skipping {st[k]['date']}.")
        pending = [k for k in pending if st[k]["decision"] is None]
        if not pending:
            break


def revise_pending(st, dry):
    """After the workflow closed the rejected issue, rewrite the entry from the feedback and
    re-dispatch the render; the new reel-draft issue then arrives like any other prompt."""
    for k, v in st.items():
        if v.get("revise") != "pending":
            continue
        try:
            state = json.loads(sh("gh", "issue", "view", k, "-R", REPO, "--json", "state"))["state"]
        except subprocess.CalledProcessError:
            continue
        if state != "CLOSED":
            continue  # post-approved.yml has not archived it yet; try again next pass
        log(f"revising {v['date']} from feedback: {v['feedback'][:80]}")
        if dry:
            continue
        try:
            subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=ROOT, check=True,
                           capture_output=True, text=True)
            out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "revise_reel.py"),
                                  f"queue/reels/{v['date']}.json", v["feedback"]],
                                 cwd=ROOT, capture_output=True, text=True, timeout=900)
            if out.returncode != 0:
                raise RuntimeError(out.stderr[-400:])
            info = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception as ex:
            log(f"  revise failed: {ex}")
            v["revise"] = "failed"
            save_state(st)
            imessage(text=f"Rewrite of {v['date']} failed ({str(ex)[:120]}). Fix it from the Mac.")
            continue
        v["revise"] = "done"
        v["outcome"] = v.get("outcome") or "revised"
        save_state(st)
        imessage(text=f"Rewrote {v['date']}: {info.get('what_changed') or info.get('hook')} "
                      f"({info.get('words')} words). Rendering now, new draft follows.")


def report_outcomes(st, dry):
    for k, v in st.items():
        if v.get("decision") and not v.get("outcome") and not v.get("feedback"):
            outcome, link = issue_outcome(k)
            if not outcome:
                continue
            log(f"issue #{k} {v['date']}: {outcome} {link or ''}")
            if dry:
                continue
            imessage(text=f"Reel {v['date']} {outcome}." + (f"\n{link}" if link else ""))
            v["outcome"] = outcome
            v["permalink"] = link
            save_state(st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    st = load_state()
    if a.status:
        for k, v in sorted(st.items(), key=lambda kv: kv[1]["date"]):
            print(f"#{k} {v['date']} decision={v.get('decision')} outcome={v.get('outcome')} {v['hook'][:50]}")
        return
    try:
        issues = open_drafts()
    except subprocess.CalledProcessError as ex:
        sys.exit(f"gh failed: {ex.stderr[-300:]}")
    prompt_new(issues, st, a.dry_run)
    read_replies(st, a.dry_run)
    revise_pending(st, a.dry_run)
    report_outcomes(st, a.dry_run)


if __name__ == "__main__":
    main()
