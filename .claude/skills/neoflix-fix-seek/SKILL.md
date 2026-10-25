---
name: neoflix-fix-seek
description: Manual-only seek/playback troubleshooting for the neoflix library — diagnosing and fixing trailing silent-audio padding that inflates a title's reported duration and breaks seek-bar accuracy (hangs, freezes, or jumps to the wrong position when scrubbing), most visible on the Tizen client. Do NOT run this proactively after every add — only invoke when the user reports an actual seeking problem on a specific title.
---

# neoflix-fix-seek: on-demand seek troubleshooting

Reactive, not part of the normal add flow. Only run it when the user
points at a specific title and says seeking is broken (hangs, freezes,
scrubs to the wrong spot). Don't scan the whole library unprompted.

## Step 1: confirm this is a duration-mismatch problem, not a transcode one

If the title plays fine but *seeking specifically* misbehaves while other
titles seek fine, check two things before going further:

- **Jellyfin's reported runtime vs the title's actual known runtime**
  (check IMDb/TMDb or general knowledge of the film/episode). A runtime
  that's noticeably longer than it should be is the signature this skill
  targets — go to Step 2.
- If the runtime looks correct, this isn't a duration-padding issue —
  check `GET /Sessions` → `TranscodingInfo.TranscodeReasons` instead
  (house rule #7 in `CLAUDE.md`); a mid-playback transcode restart on
  every seek is a different problem with a different fix.

## Step 2: compare video vs audio stream duration

Use Jellyfin's own bundled ffmpeg inside the container — it reads the
file directly from the mount, no need to go through the HTTP API:

```
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error \
  -select_streams v:0 -show_entries stream=duration \
  -of default=noprint_wrappers=1 "<container-path-to-video>"

docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error \
  -select_streams a:0 -show_entries stream=duration \
  -of default=noprint_wrappers=1 "<container-path-to-video>"
```

If the audio duration is much larger than the video duration, that
mismatch is what's driving the wrong reported runtime — Jellyfin/ffprobe
report the *longer* stream as the overall file duration, and every
client (Tizen especially) builds its seek-bar math off that number. A
quick secondary signal worth checking: a `tags.handler_name` of `GPAC
ISO Audio Handler` on the audio stream has correlated with this defect
so far — not proof by itself, but worth noting.

## Step 3: confirm it's real silence, not just a bad header value

Don't assume — a stream-copy remux will NOT fix a merely-wrong duration
*field*, but it also won't fix (and won't need to fix) actual extra
audio data. Check which one this is:

```
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -ss <a-few-seconds-before-video-end> \
  -t 10 -i "<container-path>" -map 0:a:0 -af volumedetect -f null - 2>&1 \
  | grep -E "mean_volume|max_volume"

docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -ss <a-few-seconds-after-video-end> \
  -t 10 -i "<container-path>" -map 0:a:0 -af volumedetect -f null - 2>&1 \
  | grep -E "mean_volume|max_volume"
```

Real dialogue/score just before the video's real end (double-digit
negative dB, not near the floor) followed by ~-91dB (the digital noise
floor) just after confirms genuine trailing silence padding — not a
header lie. Go to Step 4.

## Step 4: trim the padding

Do this on the host, against the host path (`${DATA_ROOT}/media/...`,
see `CLAUDE.md`'s quick reference for the container↔host path mapping).

```
ffmpeg -i "<host-path>" -map 0 -c copy -movflags +faststart \
  -t <video_stream_duration_plus_~0.1s> "<fixed-path>"
```

`<fixed-path>` is the original name with `.fixed` before the **same
extension** (`Movie.mkv` → `Movie.fixed.mkv`), so the container never
changes. Drop `-movflags +faststart` for non-MP4 containers.

**Keep `-map 0`.** Without an explicit `-map`, ffmpeg keeps only one
video and one audio stream, silently dropping extra audio tracks (for
example a boosted track from `neoflix-fix-audio`) and embedded
subtitles. Step 5 checks for exactly that.

Two things that look like they'd work but don't, confirmed the hard way:

- **A plain `-c copy -movflags +faststart` remux with no `-t` does
  nothing** — the padding is real per-frame audio data (a genuine
  million-plus AAC frames of silence in the observed case), not a stale
  header field, so stream copy carries it straight through unchanged.
- **`-shortest` does not reliably cut it either** — a known ffmpeg
  limitation with interleaved pure-stream-copy outputs; it let the audio
  stream run almost all the way to its original (wrong) length anyway.
  An explicit `-t` cutoff at the video's real duration is what actually
  works.

## Step 5: verify before touching the live file

```
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 "<fixed-path>"
for f in "<host-path>" "<fixed-path>"; do
  ffprobe -v error -show_entries stream=index,codec_type,codec_name:stream_tags=language,title -of compact "$f"
done
```

Duration should now be close to the video stream's duration from Step 2,
not the original inflated number. **The two stream lists must match**
(same number of audio and subtitle streams, same codecs and titles) — a
shorter list means a track was dropped. Don't proceed to Step 6 until
both checks pass.

## Step 6: swap it in — never overwrite in place or delete the original

Per house rule #1, `media/` and `downloads/` share disk blocks via
hardlinks — overwriting or deleting the original file in place can
corrupt or remove the other copy too. Rename, don't delete:

```
mv "<host-path>" "<host-path>.bak-before-fix"
mv "<fixed-path>" "<host-path>"
chmod 644 "<host-path>"
```

## Step 7: refresh Jellyfin and confirm before declaring victory

```
POST /Items/{id}/Refresh?MetadataRefreshMode=FullRefresh
```

Then `GET` the item again and check `RunTimeTicks` actually dropped to
match the real runtime before telling the user it's fixed — same
verify-before-declaring-victory rule as `neoflix-fix-subs`.

## Cleanup

Once the user confirms seeking works, the `.bak-before-fix` file can be
deleted to reclaim space — but check
`find "$DATA_ROOT" -samefile "<host-path>.bak-before-fix"` first (house
rule #1: it may share an inode with a `downloads/` copy). Deleting the
backup won't touch that other hardlink, but it's worth knowing it's
there before the user asks where the space went.
