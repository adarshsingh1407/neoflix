---
name: neoflix-add
description: Fast-path add for the neoflix stack. Invoke as "neoflix-add <title> <year>" for a movie or "neoflix-add <title> sXXeYY" for a show episode (e.g. "neoflix-add ted lasso s04e05", "neoflix-add as tears go by 1988"). Adds it to Radarr/Sonarr, lets built-in auto-grab run immediately, and in parallel searches for the best release under this stack's size/quality constraints — upgrading to that pick if auto-grab's choice doesn't measure up. Bails out and reports back (no download) if the episode hasn't aired yet or nothing valid is available. Skips all subtitle QA (see the neoflix-fix-subs skill for that, called manually only).
---

# neoflix-add: fast add with a constraint-checked safety net

This is the quick path: get something downloading immediately via
Radarr/Sonarr's own auto-grab, then verify/upgrade it against this
stack's actual preferences in parallel, rather than doing a fully manual
release pick up front. **Does not touch subtitles at all** — that's a
separate, manually-invoked skill (`neoflix-fix-subs`), not something this
runs automatically.

## 1. Parse the invocation

Input is free text after the command name. Determine mode:

- Ends in an `S\d\dE\d\d`-style token (case-insensitive) → **show
  episode mode**. Title is everything before it; season/episode are the
  two numbers (e.g. `s04e05` → season 4, episode 5).
- Ends in a bare 4-digit year (19xx/20xx) → **movie mode**. Title is
  everything before it; year narrows the lookup if the title is
  ambiguous.
- Neither → ask the user to clarify (movie or show, and which
  season/episode) rather than guessing.

## 2. Check it's actually out before touching anything

**For a show episode**: look it up first (`GET
/api/v3/series/lookup?term=tvdb:<id>` or by name — this works without
adding the series) and find the target episode's air date. If the air
date is in the future, or is today but likely hasn't dropped yet, **stop
here — don't add anything to Sonarr.** Report the air date (and platform
if known, e.g. via a quick web search) back to the user and end the
skill. This is a genuine "do nothing" bail-out, not a soft warning.

**For a movie**: if the lookup shows a release date in the future
(unreleased/not out yet), same bail-out — report it and stop.

Only proceed to step 3 once you're confident the thing has actually
aired/released.

## 3. Add it, letting auto-grab run

Unlike a fully manual add, here you **want** the default automatic
search to fire:

- **Movie**: `POST /api/v3/movie` with `monitored: true` and
  `addOptions: {monitor: "movieOnly", searchForMovie: true}`. Use root
  folder `/data/media/movies`, quality profile `1` (Any) unless the repo
  state says otherwise by then.
- **Show episode**: add the series first with everything unmonitored
  (`addOptions: {monitor: "none", searchForMissingEpisodes: false}` —
  Sonarr's `addOptions.monitor` silently overrides whatever you set in
  the `seasons[]` array on create, even if you also pass a season's
  `monitored: true` in the same payload, so don't trust the add
  response; verify with a fresh `GET` after). Then `PUT` the series to
  set only the target season `monitored: true`, then `PUT
  /api/v3/episode/monitor` to set only the target episode `monitored:
  true`. *Then* trigger the search Sonarr would normally do on add:
  `POST /api/v3/command {"name":"EpisodeSearch","episodeIds":[<id>]}`.
  (Doing it in this order — monitor first, search second — avoids
  Sonarr searching/grabbing for the whole series before you've scoped it
  down to one episode.)

This kicks off Radarr/Sonarr's own indexer search and download
immediately — don't wait on it before moving to step 4.

## 4. In parallel, run your own constrained search

- **Size budget**: movies ≤3GB, TV episodes ≤2GB.
- **Priority order**: hard requirements (language/audio, correct
  title/edition/episode) → source quality (real BluRay/WEB-DL over
  re-encodes; efficient codecs) → size budget → seeder health as
  tiebreaker.
- Search with `GET /api/v3/release?movieId=<id>` or `?episodeId=<id>`,
  filter out `rejected: true`, and pick your top candidate per the above.

Do this regardless of whether auto-grab has finished yet — the point is
to have an independent, constraint-checked answer ready to compare
against.

**If this search comes back with zero usable releases** (nothing at
all, or everything rejected/failing a hard requirement) — this is the
"aired but not really available yet" case. Auto-grab will have found
nothing too, for the same reason (same indexers). Don't force a bad
pick just to have something: leave the series/movie monitored (so it
picks up automatically once a real release appears) and report back
that nothing valid was found. That's the "do nothing" outcome for this
case — nothing gets downloaded, but the monitor stays armed rather than
tearing down what you just added for no reason.

## 5. Compare and reconcile

Poll `GET /api/v3/queue` and `GET /api/v3/history` until auto-grab's
pick is visible (grabbed, and ideally imported). Compare what it
actually grabbed (title/size/quality) against your own pick from step 4:

- **If auto-grab's release already meets the size budget and priority
  order** (i.e. it's the same release you'd have picked, or an
  equally-valid one) — done. Do not add a duplicate download.
- **If it doesn't** (over budget, worse source, wrong audio/language,
  etc.) — grab your recommended release too: `POST /api/v3/release`
  with `{guid, indexerId}` (remember: the response often looks like an
  empty/failed object even on success — verify via queue/history, not
  the response body). Wait for it to be grabbed and imported
  (`hasFile` true, or a `downloadFolderImported` history event).
- **Only once your recommended release is confirmed imported**, clean up
  the original auto-grabbed one if it was rejected in the comparison:
  find its torrent in qBittorrent (by name/size) and delete it **with
  its data** (checkbox in the delete dialog, or `deleteFiles=true` if
  going through Radarr/Sonarr's own delete-file endpoint for a
  since-replaced import). Don't delete it before the replacement is
  actually in place — leaving the stack without any copy while the
  better one is still downloading defeats the point of doing this in
  parallel rather than sequentially.

## 6. Make it visible

Trigger a Jellyfin library refresh for the relevant library
(`POST /Items/{libraryId}/Refresh?Recursive=true&MetadataRefreshMode=FullRefresh`)
so the final result shows up with real metadata. Stop there — no
subtitle checks in this skill.
