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
| subject photo | stock/Canva search | `scripts/subjects.py` + `scripts/subject_image.py` — `claude -p` names who each reel is about, Wikipedia/Commons supplies a CC photo (credit drawn on card) |
| editing | Canva AI | `scripts/render_reel.py` — subject photo band + edge-tts voice + PIL caption cards + ffmpeg |
| scheduling | later.com | `.github/workflows/daily-reel.yml` cron |
| cross-post | — | `scripts/yt_shorts.py` — YouTube Shorts via Dia CDP, launchd 12:40pm |

### day-to-day (fully automatic)

`daily-reel.yml` at 16:00 UTC: `next_reel.py` takes the next roadmap entry (cursor in
`state/reel_cursor.json`, one entry per run — a missed day shifts the calendar), renders it,
uploads the mp4 as a 14-day artifact, commits the staged entry + cover to `queue/reels/`, then:

- `post_mode: "auto"` → publishes as a REEL via Composio, writes the media id + permalink back
  into `queue/reels/<date>.json`, opens a `reel-log` issue with the cover so your phone gets pinged.
- `post_mode: "approve"` (the default since 2026-09-14) → opens a `reel-draft` issue with cover,
  photo/subject line + full script; comment `yes` or `no`. `post-approved.yml` downloads the
  artifact and publishes. Nothing posts without a yes.
- Phone leg: `scripts/approval_texter.py` (Mac launchd `com.sly.post4me-approve`, every 5 min)
  iMessages each draft (cover + hook + photo line) to my own iCloud thread; replying `YES` / `NO`
  there becomes the issue comment, and the permalink is texted back once Instagram confirms.
- `scripts/insights.py` → per-reel views/reach/likes/shares/saves/avg watch time from the Graph
  API into `state/insights.json` (run on the Mac, needs `composio login`).
- Photos: `scripts/subject_image.py` only uses images from the subject's own Wikipedia article
  (name in file name, no object shots, brands/gear get the gradient card). Commons search was
  removed after it put a chef eating spaghetti behind a DJ CRAFT reel.

`yt_shorts.py` (launchd `com.sly.post4me-shorts`, 12:40pm local) picks the newest posted reel
without a `youtube` field, pulls the artifact with `gh run download`, uploads through the
logged-in Dia session (`open -a Dia --args --remote-debugging-port=9223`), commits the link.

### refresh (Mac, when you want)

```
/opt/homebrew/bin/python3 scripts/competitors.py        # research/competitors.{json,md}
python3 scripts/roadmap.py --strategy                    # roadmap/STRATEGY.md
python3 scripts/roadmap.py --month 7                     # extend past month 6
```

```
python3 scripts/subjects.py                              # tag subject/subject_alt + pin a photo, every month
python3 scripts/subjects.py --month 7                    # after roadmap.py --month 7
```

`competitors.py` needs Dia logged into Instagram; `roadmap.py` and `subjects.py` need the `claude` CLI.
Each entry carries `subject` (Wikipedia title of who the reel is about), `subject_alt`, and `photo`
(the resolved Commons file + licence + credit). The render step downloads that photo and lays it
across the top ~60% of the card, feathered into a blurred copy, captions underneath, credit line above
the handle. No `photo` and no usable Commons image → the original gradient card, nothing breaks.
Only Wikipedia page images and Commons title matches are accepted (CC/PD, ≥700px, no logos/SVGs/wide
strips when a taller shot exists) — a wrong face is worse than no face.
Edit `roadmap/month-NN.json` by hand any time — entries are plain JSON. Switch modes,
voice, competitors or niche in `config.json` → `reels`.

### test a render locally

```
~/.yt-dlp-venv/bin/python scripts/render_reel.py build/stub.json build/stub
```

## the sample lane (since 2026-09-16) — the RealjN format

The static-card reels got 75–179 views each. The one C.R.E.A.M. render that reached 700 was the
same file posted from the phone app 7 hours before the API copy (127). Two lessons: the format
needs the *music*, and the phone app out-distributes the Graph API. So:

`scripts/sample_reel.py` renders a `roadmap/samples/<slug>.json` the way @RealjN does it: a hook
card, then for each pair the sampled record's own music video (center-cropped 9:16, music playing,
`ARTIST (YEAR) / "SONG" / PROD. X` burned in, a `*LOOP* / *CHOP* / *PITCHED UP*` label halfway)
cut straight into the rap record at the bar where the loop first drops. 4 pairs ≈ 47s.

- Media comes from YouTube through yt-dlp — only the artist's channel, `- Topic` auto-channels,
  VEVO or "official" uploads are accepted; the pick is pinned into the entry (`yt`) and named in
  the preview text so a wrong record can be vetoed before it posts.
- Discovery before analysis (`scripts/sample_sources.py`): WhoSampled, read through the logged-in
  Dia browser (CDP :9223, the site walls plain HTTP), says what was sampled, which element, and
  where it appears in both records ("Sample appears at 3:23"); Wikipedia's song article is the
  cross-check. That is a candidate, never the truth: `verify()` scores the original's window at the
  documented spot against the whole flip on a fine tempo/pitch grid, nudges the start within the bar,
  snaps both cuts to an onset, and `phrase_length()` runs the clip as long as the original keeps
  matching (4–9s) instead of a flat 5.5s. With no documentation the old loop-periodicity `locate()`
  runs and the pair is flagged `audio-only`. Reversed samples: pin `start` + `"locate": false`.
- Labels only with evidence: element type from WhoSampled (*REPLAYED*, *VOCAL SAMPLE*, *DRUM BREAK*,
  *THE RIFF*); `[+5% SPEED]` / `[PITCHED DOWN 2 SEMITONES]` only when the best tempo/pitch beats the
  runner-up by ≥0.03; `*REVERSED*` when the reversed original matches better. Otherwise *LOOPED* /
  *SAMPLED*. Producers and years come from the documented page when it has them.
- Confidence per pair (documented + verified + phrase + official upload) gates the cut:
  `config.json → reels.samples` (`min_confidence` 0.5 drops the pair, `auto` 0.85). Every number is
  in the preview text.
- Runs on the Mac (`com.sly.post4me-samples`, 11:30): YouTube blocks GitHub runners. `sample_daily.py`
  renders the next unposted entry, iMessages **cover + the mp4 itself + the pair notes**, and your
  YES publishes from the Mac via `publish_reel.py` (no GitHub issue). Long-press the draft's own
  message → Reply, or say `YES 2026-09-17`; a bare YES with two drafts waiting is bounced back, never
  guessed. `YES <date> tighten the cuts` = post AND keep the note; NO + words = skip and keep the
  note (`roadmap/FEEDBACK.md`). A draft never expires; an unanswered one just stays pending.
- Cache: `~/.post4me/media` (audio + the few seconds of video each clip needs).

```
~/.yt-dlp-venv/bin/python scripts/sample_reel.py roadmap/samples/wu-tang-soul.json build/samples/wu-tang-soul
python3 scripts/sample_daily.py --render-only --slug nas-illmatic     # files only, no text
python3 scripts/sample_daily.py                                        # what launchd runs
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
