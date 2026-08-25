# post4me

Two Instagram pipelines for @sl.ysl.yy, both on GitHub Actions so the Mac can be off:

1. **Daily story** (9am ET) — a 90s/00s rap quote card, approval-gated from your phone.
2. **Daily reel** (12pm ET) — a faceless voiceover + kinetic-caption reel from a 6-month
   roadmap, published automatically (or approval-gated, your call). Cross-posted to
   YouTube Shorts from the Mac.

## the reel engine

This is the "Claude + Instagram" method, with every paid tool swapped for something free:

| step | the pitch says | here |
|---|---|---|
| competitor analytics | viralfindr | `scripts/competitors.py` — Instagram's own web endpoints via the Dia session |
| 6-month roadmap | paste usernames into Claude | `scripts/roadmap.py` — `claude -p` writes `roadmap/STRATEGY.md` + 30 scripts/month |
| editing | Canva AI | `scripts/render_reel.py` — edge-tts voice + PIL caption cards + ffmpeg |
| scheduling | later.com | `.github/workflows/daily-reel.yml` cron |
| cross-post | — | `scripts/yt_shorts.py` — YouTube Shorts via Dia CDP, launchd 12:40pm |

### day-to-day (fully automatic)

`daily-reel.yml` at 16:00 UTC: `next_reel.py` takes the next roadmap entry (cursor in
`state/reel_cursor.json`, one entry per run — a missed day shifts the calendar), renders it,
uploads the mp4 as a 14-day artifact, commits the staged entry + cover to `queue/reels/`, then:

- `post_mode: "auto"` → publishes as a REEL via Composio, writes the media id + permalink back
  into `queue/reels/<date>.json`, opens a `reel-log` issue with the cover so your phone gets pinged.
- `post_mode: "approve"` → opens a `reel-draft` issue with cover + full script; comment `yes`
  or `skip`. `post-approved.yml` downloads the artifact and publishes.

`yt_shorts.py` (launchd `com.sly.post4me-shorts`, 12:40pm local) picks the newest posted reel
without a `youtube` field, pulls the artifact with `gh run download`, uploads through the
logged-in Dia session (`open -a Dia --args --remote-debugging-port=9223`), commits the link.

### refresh (Mac, when you want)

```
/opt/homebrew/bin/python3 scripts/competitors.py        # research/competitors.{json,md}
python3 scripts/roadmap.py --strategy                    # roadmap/STRATEGY.md
python3 scripts/roadmap.py --month 7                     # extend past month 6
```

`competitors.py` needs Dia logged into Instagram; `roadmap.py` needs the `claude` CLI.
Edit `roadmap/month-NN.json` by hand any time — entries are plain JSON. Switch modes,
voice, competitors or niche in `config.json` → `reels`.

### test a render locally

```
~/.yt-dlp-venv/bin/python scripts/render_reel.py build/stub.json build/stub
```

## the story pipeline

- `quotes/quotes.json` — the content bank; `state/used.json` tracks what's been drafted.
- `scripts/gen_story.py` — card renderer (6 rotating themes, Playfair Display).
- `queue/pending|posted|rejected` — the approval state machine, as folders.

## setup notes

- One repo secret: `COMPOSIO_API_KEY` (`composio login --user-api-key`).
- Composio gotcha: `INSTAGRAM_POST_IG_USER_MEDIA` has two file-uploadable fields, so `--file`
  errors out — pass the local path directly as `image_file` / `video_file` in `-d`.
- Reels are voice-only original audio. No licensed/trending music via API (app-only), and
  copyrighted audio baked into a video gets muted or struck.
- Fonts vendored in `fonts/` (Playfair Display, Montserrat — both OFL).
