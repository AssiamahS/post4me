# post4me

Approval-gated Instagram story pipeline. Runs entirely on GitHub Actions — the Mac can be off.

## how it works

1. **9am ET daily** (`daily-draft.yml`): renders the next unused quote from `quotes/quotes.json`
   onto a themed 1080x1920 story card, commits it to `queue/pending/`, and opens an issue with
   the image embedded.
2. **You, from your phone** (GitHub app): look at the draft, comment `yes` to post or `skip` to reject.
3. **`post-approved.yml`**: on `yes`, uploads the card as a story via Composio
   (create media container → publish), verifies the API said `successful: true`, moves the file
   to `queue/posted/`, and closes the issue. On `skip` it archives to `queue/rejected/`.

Only comments from the repo owner count. Anything that isn't a clear yes/no is ignored.

## layout

- `quotes/quotes.json` — the content bank (90s/00s rap quotes). Add more anytime; `state/used.json` tracks what's been drafted.
- `scripts/gen_story.py` — card renderer. 6 rotating color themes, auto text wrapping/sizing, Playfair Display (OFL, vendored in `fonts/`).
- `queue/pending|posted|rejected` — the approval state machine, as folders.
- `config.json` — IG user id, composio account selector, handle watermark.

## setup notes

- Needs one repo secret: `COMPOSIO_API_KEY` (Composio user API key; the CLI logs in with
  `composio login --user-api-key`).
- Composio gotcha: `INSTAGRAM_POST_IG_USER_MEDIA` has two file-uploadable fields, so `--file`
  errors out — pass the local path directly as `image_file` in `-d` instead.
- Stories API only takes images/videos you provide. No licensed/trending audio via API —
  that's app-only, and copyrighted audio baked into a video gets muted or struck.

## roadmap

- more series: this-day-in-hip-hop, album anniversaries, sample genealogy, versus polls
- reels: kinetic-typography quote videos (ffmpeg), original audio only
- second stage account for experiments before anything touches the main page
