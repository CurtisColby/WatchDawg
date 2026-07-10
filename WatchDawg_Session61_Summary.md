# WatchDawg — Session 61 Summary

**Session Date:** July 10, 2026 (evening, same day as Session 60)

**Baseline:** Session 60 state (Reddit source support fully operational: cookie-based fetch, feed routing, auto-download pipeline all hardware-verified; ~549 locked Reddit videos on the NAS; Vimeo 403 IP-block status unknown; queue item #2 open: "sources show 45 videos but the client only lists the 7 resolved").

**End State:** The entire Xtream + M3U catalog/playback layer overhauled and hardware-verified. Everything WatchDawg has scraped or downloaded is now visible in TiviMate; everything on disk plays instantly from the NAS via a new disk-first playback check; everything pending resolves on demand at play time. Session 60 queue item #2 (pending-video visibility on clients) is CLOSED for the Xtream/M3U layer. The Vimeo IP-block from Session 59 was observed to have LIFTED (live Vimeo resolution succeeding, no 403s).

---

## THE INVESTIGATION — three separate problems found, all fixed

### 1. Reddit auto-downloads invisible everywhere (the session's opening bug)
The Catalog web page showed "0 of 0" for Reddit sources despite the source cards counting 45+ videos. Diagnosis: Session 60's auto-download pipeline stores videos with `resolution_status="downloaded"` — a status invented AFTER both the web feed filter and the Session 52 Xtream catalog filter were written. The web Catalog exclusion is by design (downloaded = Library-only); the Xtream exclusion was a straight bug — xtream.py's own module docstring promised "Downloaded videos ARE included — they play instantly from local disk" while the actual `_eligible_video_filter` only accepted `resolved` or `local_folder`. The doc and the code disagreed; the code lost.

DB state at diagnosis (locked Reddit): 549 downloaded / 188 pending / 3 resolved — the private Xtream login could see exactly 3 Reddit items.

### 2. The bulk-downloader's local files were NEVER served (a dormant Session 42 bug)
The Session 42 mass-downloader docstring claims "the resolver automatically prefers local files over yt-dlp resolution, so downloaded videos play instantly." **That check was never implemented.** The live resolver (all 2,010 lines) contains zero references to the downloads directory. Every bulk-downloaded YouTube/Vimeo file on the NAS was dead weight at playback: TiviMate plays re-resolved the video from the internet while the local copy sat ignored. Confirmed across resolver.py, resolve.py, and channel.py — the promised path check existed nowhere.

### 3. Pending videos invisible in Xtream = YouTube unreachable in TiviMate (Colby's catch)
With background YouTube resolution permanently OFF (Session 56 cookie protection), "pending" is the permanent resting state of scraped YouTube videos — they only resolve when clicked. But the Session 52 Xtream filter hid pending videos, so YouTube content could never be clicked in TiviMate, so it could never resolve. The Session 52 rationale ("TiviMate should never wait on a live yt-dlp call") was made obsolete by Session 57's remux-on-play (hardware-verified in TiviMate) — nobody went back to update the filter. Colby identified this chain himself before deploy.

### Bonus: the auto-download "black hole"
scraper.py inserts the Video record with status "downloaded" BEFORE yt-dlp runs. A failed download leaves a phantom: invisible in every catalog, no file on disk, never retried, but counted on the source card. DB check found only 4 phantoms (545 downloads completed cleanly). Cleaned up same-session via SQL; the underlying insert-before-download pattern in scraper.py is queued as a future fix.

---

## WHAT SHIPPED — 2 files + 1 SQL cleanup

### 1. xtream.py — catalog filter aligned with reality
`_eligible_video_filter` changed from `resolved OR local_folder` to **`resolution_status != "failed"` AND NOT (pending Vimeo)** — the proven /channel/all/live.m3u baseline plus one deliberate exception. Now serves: resolved (instant), downloaded (disk-first, instant), pending non-Vimeo (on-demand resolve at play — essential for YouTube), local_folder (instant). Failed is hidden; **pending Vimeo is hidden by choice** (Colby's call after live TV testing): Vimeo background-resolves on the normal schedule, so its pendings wait for the background resolver — dead scraped links get found and auto-deleted quietly in the background instead of erroring in TiviMate, and each video appears in the catalog automatically the moment its status flips to resolved (live query, nothing to trigger). The Vimeo test keys on the URL (`source_url` contains vimeo.com), not the provider — same pattern as the resolver's YouTube exclusion — so Reddit posts linking to Vimeo are covered too.
- NOTE: two interim versions shipped and were superseded same-session: hash `8ccdefb5…` (added "downloaded" to the old OR-filter; hardware-verified — Reddit videos appeared in TiviMate) and hash `697d173e…` (plain not-failed; hardware-verified — cold Vimeo on-demand resolve + dead-link self-clean observed live). Final version below.

### 2. channel.py — disk-first playback + M3U filter alignment
- **New helper `_find_local_file_for_video()`** — the first-ever implementation of the Session 42 promise. Checks two locations: (a) any linked Favorite's `local_file_path` (Save-button + Reddit auto-downloads), (b) the bulk downloader's predictable `{downloads_path}/{Public|Private}/{channel_id}_{safe_name}/{video_id}.mp4` path — BOTH folders checked (current lock state first) so files survive a later lock toggle. Guards: realpath containment inside the downloads root (mirrors the /library/stream traversal guard), and a 1 MB minimum size to skip yt-dlp partials (same guard the bulk downloader's own skip check uses).
- **`/channel/stream/{id}` disk-first branch** — runs BEFORE the failed-status 404 (a file on disk is playable regardless of what happened to its online source). File found → 302 to `/library/stream/{relpath}`, zero yt-dlp, zero cookies, instant start. No file → falls through to the exact pre-existing resolver path, unchanged. New guard: status "downloaded" with no file on disk → clean 404 (never resolve a hostile-CDN source_url).
- **Public + private M3U playlists** — filter changed to `!= "failed"` AND NOT (pending Vimeo), matching the Xtream catalog exactly. Lock split untouched: public playlist still loops only unlocked channels, private only locked, so locked-source content structurally cannot appear in public (Colby's explicit requirement — verified by construction and to be eyeballed on hardware).

### 3. Deploy-time SQL — 4 phantom records deleted
Failed auto-download videos + their favorites removed. Posts are not skip-listed, so the next scrape re-discovers and retries them automatically. First attempt hit "database is locked" (startup-scrape write contention) — resolved with `.timeout 30000`.

---

## LIVE FILE HASHES AFTER THIS SESSION (source of truth)

| File | Container path | New SHA-256 |
| --- | --- | --- |
| xtream.py | /app/app/routers/xtream.py | 12f5d5081747508167c14be8a269753c23260c6c59d89d671707ce95ba34620d |
| channel.py | /app/app/routers/channel.py | e39441173c0c0b5fedd6c10267c4062ae83b67aaac4c3d488271e998f8d4013b |

Pre-edit three-way hash checks passed on both files (container==host==GitHub; xtream.py `4fb83910…`, channel.py `9836a6db…`). Both new files pushed to ~/watchdawg-repo-sync → GitHub at session end. Reminder: ~/watchdawg-modified-backup/ still holds the standalone xtream.py backup — never delete that directory.

---

## HARDWARE VERIFICATION (from live TiviMate testing + container logs)

- **Disk-first CONFIRMED:** videos 8664 and 8863 logged `disk-first → Private/7_PURE_CHOKALATE/{id}.mp4` and played instantly — first-ever local-file playback of bulk-downloaded content.
- **Cold Vimeo on-demand resolve CONFIRMED:** video 28367 resolved live in ~3.4s, proxied, split-HLS master manifest, played. **No 403 — the Session 59 Vimeo IP-block has LIFTED.**
- **Self-cleaning CONFIRMED:** video 8519 returned Vimeo HTTP 404 (deleted upstream) → classified permanent → record auto-deleted → 502 to client. Dead scraped links bury themselves on first play. Expect scattered one-time errors early on as the newly-exposed catalog self-cleans; TiviMate caches its catalog, so refresh the source to make buried corpses disappear.
- **STILL OPEN:** cold YouTube play through the Xtream login (the 15–40s resolve+remux — the headline feature), the public-login lock-discipline eyeball, and v.redd.it playback (carried from Session 60).

---

## KEY LEARNINGS THIS SESSION

- **When a docstring and its code disagree, the intent was probably lost in a later change — treat the doc as the spec and the code as the bug.** Happened TWICE this session: xtream.py's "downloaded ARE included" (filter predates the status) and channel.py's "resolver automatically prefers local files" (never implemented at all).
- **Every new resolution_status needs a grep across every filter in the codebase.** "downloaded" (Session 60) silently broke three catalog surfaces because the filters predate it. Same class of bug as the poison-write pattern: new state, old gates.
- **A filter's rationale has an expiry date.** Session 52's "never make TiviMate wait on yt-dlp" was correct until Session 57 built remux-on-play — then it became the exact mechanism starving the on-demand architecture. When an architectural assumption changes, audit the decisions that were built on it.
- **The Session 60 open question is answered:** the background-resolve YouTube exclusion keys on the URL (`youtube.com`/`youtu.be` contains-match at the query level), NOT the provider — so Reddit-provider rows linking to YouTube are correctly excluded from scheduled resolution. No cookie leak.
- **Scheduled resolution now covers pending Vimeo/Reddit automatically:** 25/tick, YouTube excluded, Vimeo paced 5–10s with the 5-strike circuit breaker. The 188 pending Reddit + pending Vimeo resolve themselves over the coming days; only YouTube stays on-demand-only, by design. Batch order is reddit_score DESC, so scored Reddit posts resolve ahead of unscored Vimeo.
- **Per-provider catalog policy is legitimate and now established:** providers that background-resolve (Vimeo) hide their pendings and let the resolver taste-test for dead links; providers that only resolve on demand (YouTube) must show their pendings or they're unreachable. The deciding question for any future provider: "does anything resolve this in the background?"
- **"database is locked" right after a restart is the startup scrape**, not corruption — retry with `.timeout 30000` or wait out the scrape.

---

# NEXT SESSIONS — QUEUE

1. **Progressive YouTube streaming (high priority, carried):** byte-piped progressive streaming so first-play startup latency stops scaling with video length (~15s regardless of duration). Now MORE valuable: every cold YouTube play in TiviMate goes through the full-download remux path.
2. **Verify cold YouTube play through Xtream in TiviMate** (left open this session) + public lock-discipline eyeball + v.redd.it playback test (carried from Session 60).
3. **Fix the auto-download black-hole pattern in scraper.py:** insert-before-download leaves phantom "downloaded" records on yt-dlp failure. Either insert after success, or mark failures back to a retryable state. (Only 4 occurred out of 549 — low urgency, real pattern.)
4. **Reconcile job (optional now):** scan download folders and stamp on-disk pending videos (e.g. set resolved_stream_url → /library/stream path like local_folder does) so catalog entries reflect their local copies with correct instant-play metadata. Less urgent since disk-first playback already serves the files; value is metadata accuracy and skipping the resolver entirely for on-disk pendings that lack a Favorite record.
5. **Web UI Catalog visibility decision (carried from this session's opening bug):** downloaded videos are Library-only in the web Catalog by design, but source cards count them — consider a "Saved to NAS" badge in the Catalog or a count split on the source card so the numbers stop looking like a bug.
6. **6-hour YouTube temp cache, WatchDawg pseudo-channels, PIN-lock web-UI removal, clone count, dead-file cleanup (two scheduler.py copies + stray index.html), resolve_video_for_tv force-parameter bug** — all carried unchanged.
