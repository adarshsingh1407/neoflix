---
name: neoflix-fix-audio
description: Manual-only audio troubleshooting for the neoflix library — measuring a title's loudness against the library norm and, if it is too quiet (or the user asks for it), adding a parallel dialogue-forward, loudness-normalised audio track alongside the original so it can be picked in Jellyfin. Never replaces or removes the original audio. Do NOT run this proactively after every add — only invoke when the user reports a specific title sounds too quiet/muffled or explicitly asks for a dialogue-boosted track.
---

# neoflix-fix-audio: add a louder, dialogue-forward audio track

Reactive, not part of the normal add flow. Only run it when the user
points at a specific title and says the volume is too low, the dialogue is
hard to hear, or asks outright for a boosted track. Don't scan the whole
library unprompted.

**The original audio track is never touched.** The fix is a *second*
audio stream added to the file; the original stays, stays the default,
and stays selectable.

## Step 1: measure — don't guess, and don't blame 5.1

"Sounds quiet" on a 5.1 file is usually a quiet *master*, not the channel
layout. Measure first, using Jellyfin's bundled ffmpeg (the container
mount is read-only, so this is analysis only):

```
FF=/usr/lib/jellyfin-ffmpeg/ffmpeg
FP=/usr/lib/jellyfin-ffmpeg/ffprobe
docker exec jellyfin $FP -v error \
  -show_entries stream=index,codec_type,codec_name,channels,channel_layout:stream_tags=language,title \
  -of compact "<container-path>"
docker exec jellyfin $FF -hide_banner -nostats -i "<container-path>" -vn -map 0:a:0 \
  -af ebur128=peak=true -f null - 2>&1 | sed -n '/Summary:/,$p'
```

Read integrated loudness (`I:`) and `Peak:`. For 5.1 sources also look at
per-channel levels (`-af astats=metadata=0`; order FL FR FC LFE BL BR) —
a healthy centre channel (the dialogue) and a silent LFE is normal.

- **Library norm is about -25 LUFS** (several films measured -25 to -26).
- **Quieter than about -29 LUFS** → too quiet, fix it. (A real case
  measured -35.7 LUFS: ~10 dB below the rest of the library, with a
  loudest peak of only -13.7 dBFS.)
- **Between about -29 and -20** → the level is fine. Report the numbers
  and stop, unless the user explicitly asked for a dialogue-boosted track
  anyway — in that case continue, but see the target note in Step 2.
- **Louder than about -20 LUFS** → the title is already loud; a boost
  isn't warranted. Only build a track if the user explicitly asks, and
  then it is mostly compression (quiet lines up, loud scenes held down):
  keep `I` at or above the original's level and say that it won't sound
  louder overall.

**Pitfall:** don't test a stereo downmix with `-ac 2` on the output. That
option is applied *after* the filter graph, so `ebur128` still measures
the original channels and gives you identical numbers. Downmix inside
the filter (the `pan` below, or `aresample=ocl=stereo`) before measuring.

## Step 2: choose the filter chain

The new track is stereo AAC, so it plays everywhere without a server-side
downmix. Pick the chain for the source layout:

- **5.1** (use channel indices `c0..c5` = FL FR FC LFE BL BR, which avoids
  `5.1` vs `5.1(side)` naming differences) — weight the centre up:
  `pan=stereo|c0=0.8*c0+0.9*c2+0.4*c4|c1=0.8*c1+0.9*c2+0.4*c5`
- **Stereo** (no centre channel, so "dialogue boost" is only an
  approximation — say so): lift the centred mid content and trim the sides,
  then add speech presence and gentle compression:
  `pan=stereo|c0=1.06*c0+0.35*c1|c1=0.35*c0+1.06*c1,equalizer=f=2500:t=q:w=1:g=3,acompressor=threshold=0.05:ratio=3:attack=10:release=200:makeup=2`
- **Mono**: `pan=stereo|c0=c0|c1=c0`, then the stereo chain's EQ and
  compressor.

Finish every chain with a **two-pass `loudnorm`** (full commands in
Step 3), then `aresample=48000`. loudnorm resamples internally, so the
explicit `aresample` matters.

**Target loudness:**
- Quiet master (below about -29): `I=-24:TP=-1.5:LRA=12`. A pure volume
  gain to -24 would push peaks past full scale: on the real case the
  dialogue-forward downmix measured -33.8 LUFS with a -9.2 dBTP peak (a
  little hotter than the 5.1 mix's -35.7 LUFS, since the downmix sums the
  centre into both channels), so +9.8 dB would land at about +0.6 dBTP.
  loudnorm's dynamic mode gets close (about -24.7 LUFS on the real case)
  without clipping.
- Already-fine level but the user wants a dialogue boost: set `I` **at or
  above the original's integrated loudness**, not below it. A first
  attempt that targeted -21 against a -19 original produced a track ~2 dB
  *quieter* overall; it only helped quiet passages. If it ends up that
  way, tell the user plainly.

## Step 3: encode the new track

Encode inside the Jellyfin container (write to its `/tmp`), copy out to a
scratch folder on the host. Pass 1 only measures; **it must use exactly the
same `<chain>` as pass 2**:

```
# pass 1: measure (read input_i, input_tp, input_lra, input_thresh, target_offset)
docker exec jellyfin $FF -hide_banner -nostats -i "<container-path>" -vn -map 0:a:0 \
  -af "<chain>,loudnorm=I=-24:TP=-1.5:LRA=12:print_format=json" -f null - 2>&1 \
  | sed -n '/^{/,/^}/p'

# pass 2: encode using those numbers
docker exec jellyfin $FF -y -i "<container-path>" -vn -map 0:a:0 \
  -af "<chain>,loudnorm=I=-24:TP=-1.5:LRA=12:measured_I=<input_i>:measured_TP=<input_tp>:measured_LRA=<input_lra>:measured_thresh=<input_thresh>:offset=<target_offset>:print_format=summary,aresample=48000" \
  -c:a aac -b:a 192k -ac 2 /tmp/boost.m4a
docker cp jellyfin:/tmp/boost.m4a <scratch>/boost.m4a
```

(Change `I=-24` per the target notes above.)

Verify before touching the library: `ebur128` on the new file (expect
close to the target, peak at or under about -1 dBFS) and
`ffprobe format=duration` — it **must equal the video's duration** so the
track stays in sync.

## Step 4: mux it in as a second track

Jellyfin's `/data/media` mount is **read-only**, so write from the host
(`/opt/homebrew/bin/ffmpeg`). Stream-copy everything — no re-encode of
video or the original audio — into a temp file in the same folder:

```
/opt/homebrew/bin/ffmpeg -y -i "<original>" -i <scratch>/boost.m4a \
  -map 0:v:0 -map 0:a -map 1:a:0 -map "0:s?" -c copy \
  -metadata:s:a:<n> title="Dialogue boost (louder, compressed)" \
  -disposition:a:0 default -disposition:a:<n> 0 \
  -movflags +faststart -f mp4 "<folder>/.audio_new.tmp"
```

**Quote `"0:s?"`.** Unquoted, zsh treats the `?` as a glob and aborts the
whole command ("no matches found") before ffmpeg runs.

`<n>` is the new stream's audio index (one past the original audio
tracks). Use `-f matroska` for `.mkv`. Always `-map 0:a` so *every*
original audio stream is kept. Label an untitled original "Original".

**Embedded subtitles and `-f mp4`:** the mp4 muxer can't carry SRT/ASS
subtitle streams, so a source with them fails with "Could not write
header" (safely — no output file is written). Switch to `-f matroska`
and an `.mkv` extension only if that is acceptable, otherwise drop
`-map "0:s?"`, and say which streams you left out. Most files here keep
subtitles as an external `.srt`, which this never touches.

`ffprobe` the temp file **before** swapping: original audio still present
with unchanged codec/channels, new stereo AAC present, duration identical,
video codec unchanged.

**Confirm a rollback copy exists before replacing the library file:**

```
stat -f "%l links" "<original>"
```

- **2 or more** → a `downloads/` copy shares the inode, so it keeps the
  old content after the swap. That is the rollback. `mv -f` the temp file
  over the original.
- **1** → nothing else holds the original (the torrent was already
  removed). Do **not** `mv -f` over it: rename it to
  `<original>.bak-before-audio-fix` first, then move the temp file into
  place. Tell the user the backup costs the file's full size until they
  confirm the new track works; only then delete it.

## Step 5: make it visible, and tell the user the tradeoffs

- `POST /api/v3/command {"name":"RescanMovie","movieId":<id>}` on Radarr
  (Sonarr: `RescanSeries`), then refresh the item in Jellyfin
  (`POST /Items/{id}/Refresh?MetadataRefreshMode=FullRefresh&ImageRefreshMode=None`)
  and confirm via `GET /Items?ids={id}&Fields=MediaStreams` that both audio
  tracks are listed and the original is still `IsDefault`.
- **The library file becomes an independent copy.** The swap breaks the
  hardlink to `downloads/`, so the title takes extra disk (the original
  file size plus roughly 150–200 MB for the track). The untouched copy in
  `downloads/` is the rollback (or the `.bak-before-audio-fix` file if
  there was no download copy). Say so, and offer to delete the torrent and
  its download copy if the user wants the space back.
- A small extra `bin_data` stream (a chapter text track) can appear;
  harmless.
- The numbers come from **analysis, not listening**. Ask the user to try
  a dialogue-heavy scene, and offer to re-render louder or with a
  different target. A normalised, compressed track has less dynamic range
  than the original — that is the point, but mention it.

## Gotchas

- zsh aborts a whole command on an unmatched glob and doesn't word-split
  unquoted variables — use explicit paths or wrap scripts in `bash -c`.
- A safety check blocks `rm` with glob or variable paths. Delete only
  exact scratch files you created; never delete the user's media.
- Keep scratch files out of the repo and out of `media/`/`downloads/`.
- API keys are in `.env` / the apps' config files — never paste a key
  value into a doc, commit message or skill file.
