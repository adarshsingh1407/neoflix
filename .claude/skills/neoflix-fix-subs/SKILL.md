---
name: neoflix-fix-subs
description: Manual-only subtitle troubleshooting for the neoflix library — junk/ad-placeholder detection, extracting embedded ASS/PGS to plain SRT to avoid transcode-on-playback, sync verification against actual video duration, and finding/downloading a better subtitle via Bazarr. Do NOT run this proactively after every add — only invoke when the user reports an actual subtitle problem (missing, not showing, out of sync, wrong text) on a specific title.
---

# neoflix-fix-subs: on-demand subtitle troubleshooting

This is reactive, not part of the normal add flow (`neoflix-add`
deliberately skips all of this). Only run it when the user points at a
specific title and says something's wrong with its subtitles. Don't
scan the whole library unprompted, and don't run this automatically
after `neoflix-add` finishes.

## Step 1: figure out which failure mode this is

Ask yourself (check the file, don't guess):

- **"Subtitles don't show at all" / had to enable manually** → check
  `DefaultSubtitleStreamIndex` and the user's `SubtitleMode` in
  Jellyfin. If nothing's flagged default and `SubtitleMode` is
  `Default`, nothing will auto-enable regardless of the file being
  fine. Fixing the account's `SubtitleMode` to `Smart` (with
  `AudioLanguagePreference`/`SubtitleLanguagePreference` set to the
  user's language) via `POST /Users/{userId}/Configuration` is a
  one-time systemic fix, not a per-file one.
- **"Selected it but still nothing rendered" / buffering the moment
  subtitles turn on** → almost certainly an embedded ASS/PGS-only track
  forcing a full server-side transcode (Jellyfin can't deliver
  styled/image subtitles to most clients without burning them into the
  video). Go to Step 2.
- **"Text shows but it's out of sync"** → compare the subtitle's last
  timestamp against the actual video duration first (see Step 3) before
  touching anything — don't assume a fixed offset will fix it.
- **"Wrong words / bad translation / doesn't match at all"** → Step 4,
  and read the caution there before swapping the whole file.

## Step 2: embedded ASS/PGS → extract to plain SRT

Find the stream:
```
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v quiet -print_format json \
  -show_streams "<container-path-to-video>"
```
Look for `codec_type: subtitle` entries; prefer the one whose
`tags.title` reads like the main dialogue track (e.g. "Full Subtitles"
over "Signs" or "Blu-ray Subtitles"/PGS).

Extract and clean:
```
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -y -i "<container-path>" \
  -map 0:<index> /tmp/out.srt
docker cp jellyfin:/tmp/out.srt /tmp/out.srt
```
Strip leftover ASS styling that survives SRT conversion — `<font>`,
`<b>`, `<i>`, `<u>` tags, and stray `{\anX}`-style override codes — with
a regex pass, then save as `<video-basename>.eng.srt` next to the video
on the host.

**If the release splits dialogue and signs/songs into two separate
tracks meant to be shown together** (a real convention, not a
duplicate), extract both, parse into `(start_ms, text)` tuples, sort
merged by start time, and write one combined file — don't drop one
track.

After saving, force a refresh on the specific Jellyfin item
(`POST /Items/{id}/Refresh?MetadataRefreshMode=FullRefresh`) and confirm
the new external track shows up in `MediaStreams` with `IsExternal:
true` before telling the user it's fixed.

## Step 3: verify sync before declaring victory

```
ffprobe -show_format <video>   # -> format.duration
```
Compare against the subtitle's last `-->` timestamp. A gap of more than
a few minutes beyond a plausible end-credits window means the subtitle
was authored for a **different cut/release**, not a framerate issue —
don't try to linearly rescale it. Go straight to Step 4 for a real
replacement instead of trying to force-fix a mismatched source.

If asked to try Bazarr's built-in sync (ffsubsync) anyway: it is **not**
reliable enough to trust blind — it can make timing *worse*, especially
on films with long dialogue-free stretches (bad audio-correlation
anchor). Always re-check against actual duration afterward, and be
ready to revert.

## Step 4: finding a real replacement via Bazarr

Bazarr's own API key lives under `auth:` in
`config/bazarr/config/config.yaml` — **not** the same key used for
Radarr/Sonarr, which are also in that file under their own sections.

```
GET /api/providers/movies?radarrid=<id>&hi=false&forced=false&language=en
```
returns scored candidates with `matches`/`dont_matches` arrays.

- A **`hash` match is the only real guarantee** of correct sync/cut for
  this exact file — trust it over everything else.
- Absent a hash match, prefer a candidate whose `release_info` names the
  **same release group/filename pattern** as the file already in the
  library (e.g. same `YTS.AM` tag) — much safer than a high metadata
  score with a different source.
- **Always back up the current subtitle file before overwriting** —
  Bazarr does not keep its own backup of what it replaces.

Download with:
```
POST /api/providers/movies
{radarrid, hi, forced, language, provider, subtitle, original_format}
```

**If the new download turns out to not match either** (wrong cut, still
out of sync) — this can genuinely happen when no hash-matched option
exists. Revert to the backup rather than trying a third blind guess;
the embedded/original subtitle is often the only one actually
guaranteed to match this specific file, even if its wording isn't
perfect.

## Step 5: reported typos/mistranslations — don't swap the whole file

If the complaint is "a few words are wrong" rather than "doesn't
match," **do not** reflexively replace the whole subtitle — a full swap
risks trading a minor wording issue for a real cut/sync mismatch (this
has happened). Instead:

1. Find the reported line (`grep` for the text, or the timestamp shown
   in a screenshot).
2. Read a reasonable stretch of the surrounding file yourself — this
   class of error (structurally valid but semantically wrong sentences,
   stray non-ASCII characters like a stray `|` for "I", capitalization
   mid-sentence, a stray digit typo) won't reliably show up in a
   grep/regex pass, it needs actual reading comprehension.
3. Hand-fix the specific entries in place with a direct string
   replacement. No rescan needed afterward — Jellyfin reads subtitle
   files live per-request, not from a cache.
4. While you're in there, a quick pass for pervasive *cosmetic*
   inconsistencies (e.g. some dash-led dialogue lines missing the space
   other lines have: `-Yes.` vs `- Yes.`) is reasonable to normalize in
   the same pass if the user asks for it — but don't go looking for
   this unprompted either; it's cosmetic, not a functional bug.

## Junk/ad-placeholder files (worth checking regardless of complaint type)

Some scene-group and YTS releases ship a fake subtitle that's just
tracker ads (`Provided by YTS.MX`, `Downloaded from ...`, `YIFY movies
site`) with only a handful of entries, sometimes mixed with real
end-credits text. Quick test: `grep -c '\-\->' file.srt` — a
suspiciously low count for the runtime (rule of thumb: under ~50 entries
for a 90+ minute movie) is a red flag. Read the file to confirm before
trusting it; a plausible-looking last-timestamp is not sufficient proof
of a real subtitle, since ad/credits text can coincidentally span most
of the runtime. If it's junk (or has junk entries mixed into otherwise-
real content), strip the ad blocks or replace the file via Step 4 —
don't leave it as the only subtitle option.
