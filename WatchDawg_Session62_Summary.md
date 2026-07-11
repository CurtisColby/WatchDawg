# WatchDawg — Session 62 Summary

**Session Date:** July 11, 2026

**Baseline:** Session 61 state (Xtream + M3U catalog/playback layer overhauled: disk-first playback, pending-non-Vimeo visible, everything hardware-verified except cold YouTube play through Xtream; Vimeo IP-block lifted; ~549 locked Reddit auto-downloads on the NAS).

**End State:** Two features shipped. (1) Progressive YouTube streaming: cold YouTube plays now pipe ffmpeg output live to the client instead of downloading the whole video first — startup latency no longer scales with video length; the Session 57 full-download remux remains as an automatic fallback. NOT yet hardware-verified. (2) Reddit library management: the thumbnail pipeline for auto-downloaded files was diagnosed and fixed across three files, hardware-verified — all Reddit downloads now show previews in Files on Disk. Along the way, discovered that delete-and-ban and per-source pause ALREADY existed and work for Reddit content.

---

## TASK 1 — Progressive YouTube streaming (channel.py)

### The problem
The Session 57 remux-on-play path resolved the stream, then threw the answer away and ran yt-dlp a SECOND time to download the entire video to /tmp before serving byte one. Startup scaled with video length (a 2-hour video could take minutes or blow the 300s ceiling), every play burned two cookie-bearing YouTube extractions, and long videos briefly ate gigabytes of /tmp.

### What shipped
New helper `_try_progressive_youtube_pipe()` + pipe-first wiring in the `is_youtube` branch of `/channel/stream/{id}`:
- The two ALREADY-RESOLVED CDN URLs (video-only + audio-only) are fed straight into ffmpeg (`-c copy`, no transcode) with fragmented-MP4 output (`-movflags frag_keyframe+empty_moov+default_base_moof`) piped to the client via StreamingResponse. Startup ≈ resolve time + a couple of seconds regardless of duration.
- Per-input browser user-agent + auto-reconnect flags so a momentary CDN hiccup mid-movie resumes instead of ending the stream.
- Client disconnect (stop / channel change) kills ffmpeg immediately — no orphaned processes; nothing ever touches disk. A background stderr-drain task prevents pipe-buffer deadlock on chatty long streams.
- **Safety net:** if ffmpeg produces no output within 20s (suspects: googlevideo rejecting ffmpeg's request style, or container ffmpeg lacking HTTPS), the helper returns None and the UNCHANGED Session 57 full-download remux runs. Worst case is the old behavior, never a broken player. Log markers: `STREAM PIPE` (new path) vs `STREAM REMUX` (fallback).
- Side benefits: one cookie-bearing extraction per play instead of two; zero temp-file usage on the pipe path.
- **Accepted trade-off:** a live pipe is not seekable — skipping on a cold first play restarts the stream (`Accept-Ranges: none` declared honestly). The queued 6-hour temp cache is the future fix that gives instant start AND seek.
- Route docstring updated to match (doc = spec).

## TASK 2 — Reddit library management (library.py, scraper.py, index.html)

### Colby's ask
See auto-downloaded Reddit content with thumbnails, delete individual items with a re-download ban, and pause a source from grabbing more.

### The discovery: two-thirds already existed
- **Delete + ban:** the 🗑️ button in Downloads → Files on Disk already deletes the file, adds the post to the skip list (scrapes can never re-download it), and removes the DB records + thumbnail. Works for Reddit files via their Favorite links.
- **Pause:** the "Enabled" toggle on each source card IS the pause button — the scheduler and Scrape All only touch enabled channels, and Reddit auto-downloads only happen during a scrape.
- **Disk protection:** `REDDIT_DOWNLOAD_CAP_PER_SUB = 500` counts LIVE files in the folder at download time — deleting 10 files frees 10 slots for NEW posts next scrape (deleted ones are skip-listed and structurally can't return).

### The real gap: thumbnails — two compounding bugs
1. **Listing bug (library.py):** for files WITH a DB record (every Reddit auto-download), `list_library` used only the DB's `thumbnail_url` (often null for Reddit — NSFW placeholders, hostile CDNs) and NEVER checked disk for a generated sidecar thumbnail; the sidecar fallback only ran for record-less files. Same disease as Session 61: new content path, old code branch.
2. **Generation gap:** the live `/library/generate-thumbnails` had been rewritten at some point to be DB-driven and `local_folder`-ONLY — it no longer walked the download folders at all, so it could never touch a Reddit (or bulk-downloaded) file. Nothing generated thumbnails for downloads automatically.

### What shipped
1. **library.py** — (a) Listing: sidecar-first thumbnail for ALL files, DB URL as fallback. (b) `generate-thumbnails` gained a pass 2: after the unchanged local_folder DB pass, walk the download folders and generate a sidecar for ANY video file lacking one (Reddit, bulk, Save-button downloads), sharing the per-run limit. No DB write-back needed — the listing fix reads sidecars directly. Genre-pill feature (found in the live file, absent from the stale project copy) untouched.
2. **scraper.py** — after each successful Reddit auto-download, the sidecar thumbnail is generated immediately (off-thread, non-fatal on failure; the pass-2 walk retries stragglers). Method docstring step list updated.
3. **index.html** — Generate Thumbnails bumped from 20 to 200 per click (both invocations + tooltip); 549-file backfill = 3 clicks.

### Hardware verification
- **CONFIRMED:** after deploy + Generate Thumbnails runs, Reddit auto-downloads display thumbnails in Downloads → Files on Disk across subreddit folders.
- Clarified in-session: the Catalog page hiding downloaded Reddit content is the Session 61 DESIGN ("downloaded = Library-only" in the web Catalog) — the "No videos match" empty state Colby saw was the Catalog, not Files on Disk. The one source showing a few items was its pending/resolved videos, which the Catalog does show.

---

## LIVE FILE HASHES AFTER THIS SESSION (source of truth)

| File | Container path | New SHA-256 |
| --- | --- | --- |
| channel.py | /app/app/routers/channel.py | ca967667079ce846c47db23ce25010095dd274f1a3cd67b355d08be9c0882e3d |
| library.py | /app/app/routers/library.py | 1c7cf1c469535da766c16f4d195283346ed81455607b15c26f32bcb99ff9fed6 |
| scraper.py | /app/app/services/scraper.py | 3759262010d43263e93f0948fd74eddd7b88f5278c28f1982cc599f9db082e5a |
| index.html | /app/app/templates/index.html | 4ed5a792ad7bfcc05cb0fb23817317a5c82740418af94743c263a605a7f2b0b4 |

Pre-edit three-way checks: channel.py `e39441…`, scraper.py `c30c76…` — all clean. library.py: container==host==GitHub `dc8073…` but the PROJECT copy was stale (`a6130d…`, predating the genre-filter feature) — halted per Freshness Rule; Colby uploaded the live file, hash-verified before building. index.html: container==host==project `5a2879…` but GITHUB was behind (`db6b40…`) coming into this session — this session's push heals it.

---

## KEY LEARNINGS THIS SESSION

- **The stale-project-copy hazard struck TWICE and the hash checks caught both:** scheduler.py's project copy was pre-Session-59 (old batch limit, missing jobs — luckily not needed), and library.py's was pre-genre-filter. A plan built on the stale library.py would have designed against a generate-thumbnails implementation that no longer existed. Never design edits from an unverified copy.
- **An endpoint's behavior can silently narrow:** generate-thumbnails started life as a folder walk and was later rewritten as local_folder-DB-only — correct for its new purpose, but it orphaned every other file type on disk. When repurposing shared-sounding endpoints, check who else depended on the old behavior.
- **Audit both branches when adding a fallback:** the sidecar-thumbnail fallback existed but only in the "no DB record" branch; content that always HAS a record (Reddit auto-downloads) could never reach it.
- **Two features Colby asked for already existed** (delete+ban, Enabled-as-pause). Before building, inventory what's already shipped — Sessions 42–61 left a lot of working machinery behind.
- **The 500-file subreddit cap is self-healing:** it counts live files at download time, so curation (deleting unwanted files) automatically makes room for fresh content while the skip list guarantees deletions are permanent.
- **fMP4 vs plain MP4:** normal MP4's index lives at the end of the file and cannot be piped; `frag_keyframe+empty_moov` fragmented MP4 is the streamable flavor, still `video/mp4`, no transcode needed.

---

# NEXT SESSIONS — QUEUE

1. **Hardware-verify progressive YouTube streaming (open from this session):** cold-play a SHORT then a LONG (1hr+) YouTube video in TiviMate — both should start in ~15s; log should show `STREAM PIPE … first bytes ready`. If instead `falling back to full-download remux` appears, paste the logged ffmpeg error (suspects: googlevideo rejecting ffmpeg requests, or container ffmpeg lacking HTTPS support). Also confirm no orphaned ffmpeg after stopping playback. This subsumes the carried "cold YouTube play through Xtream" test.
2. **Catalog integration decision (queue #5, sharpened this session):** Colby may want downloaded Reddit files browsable in the web Catalog too, with thumbnails and delete buttons. Would require: feed filter change, writing sidecar thumbnail URLs back to Video records (Catalog reads DB, not disk), and delete buttons on downloaded-item cards. Decision pending — Files on Disk deemed "good" for now.
3. **Carried opens:** public-login lock-discipline eyeball; v.redd.it playback test (from Session 60).
4. **6-hour YouTube temp cache (40 GB cap):** now also the fix for the pipe path's no-seek trade-off (tee to disk while piping).
5. **Auto-download black-hole pattern in scraper.py** (insert-before-download phantom records) — carried.
6. **Reconcile job, "Saved to NAS" badge/count split on source cards, WatchDawg pseudo-channels, PIN-lock web-UI removal, clone count, dead-file cleanup (two scheduler.py copies + stray index.html), resolve_video_for_tv force-parameter bug** — all carried unchanged.

**Housekeeping:** refresh the Claude project files — scheduler.py and library.py copies are confirmed stale; upload the current channel.py, library.py, scraper.py, index.html, and this summary.
