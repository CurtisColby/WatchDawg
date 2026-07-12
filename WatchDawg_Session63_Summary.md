# WatchDawg — Session 63 Summary

**Session Date:** July 11, 2026

**Baseline:** Session 62 state (progressive YouTube streaming shipped but not hardware-verified; Reddit thumbnails fixed and verified; Files on Disk folder gallery shipped).

**End State:** Full read-only audit completed on hash-verified live files, followed by a seven-file fix-and-feature arc. Every planned defect fixed, scheduler dashboard shipped, Problem Videos view shipped, two zombie buttons removed, EPG auto-rebuild finally scheduled. Files 1–4 container-hash-verified in session; index.html + feed.py delivered with hashes (verify on deploy).

---

## THE AUDIT (Phase A — read-only, verified files only)

Phase 0 freshness check: 16 stale/never-seen files uploaded from the container and hash-verified against the `docker exec … sha256sum` listing before a single line was read. Project-knowledge copies of library.py, resolver.py, main.py, models.py, database.py, health.py, proxy.py, web_ui.py, and scheduler.py were all confirmed stale; epg.py, feed_work.py, transcode.py, providers/base.py, providers/playlist.py, tasks/pseudo_scheduler.py, tasks/scheduler.py had never been in the project at all.

### Findings (all verified against live code)

1. **Zombie buttons.** Nothing has written `resolution_status="failed"` since the Session 59 poison-write fix (permanent errors auto-delete; transient errors stay pending with error text). "Purge Dead Links" and "Reset Failed Videos" operated exclusively on legacy rows — permanent no-ops going forward. The purge-dead confirm dialog also falsely claimed it "checks" links; it never checked anything.
2. **"Resolve 25 Pending" bypassed every guard.** The `/resolve/batch` router endpoint was a fossilized duplicate of old batch logic: no YouTube exclusion (burned cookies against the resolve-on-play policy — Colby's report confirmed), no back-off/cookie-stale guards, no circuit breaker, and an "expired pass" that refreshed `resolved_stream_url` via `resolve_video()` — which never writes `resolved_audio_url` — leaving mismatched URL pairs for the TV cache to serve.
3. **`?force=true` silently ignored on the TV path** (`resolve_video_for_tv` had no force parameter).
4. **Delete paths stranded child rows.** `Favorite` has no CASCADE; `WatchHistory`/`Watchlist` declare CASCADE but SQLite never enforces it (no `PRAGMA foreign_keys=ON` anywhere). Raw `db.delete(video)` orphans all three — and SQLite reuses rowids, so an orphaned watch-history row could later attach to an unrelated new video.
5. **EPG schedules silently expired.** `rebuild_all_epg_schedules()` says "called by the scheduler every 6 hours" — no such job existed. Schedules build 48 h out; Live TV went blank ~2 days after the last manual rebuild. `refresh_all_xmltv_sources()` ("every 2 hours") likewise never called.
6. **`warm_tv_cache()` was fully built and called by nothing** (only reference: a comment). Deleted per decision.
7. **Dead files:** `/app/app/scheduler.py` (main.py imports `app.tasks.scheduler` — the root copy is dead), `routers/feed_work.py`, `routers/transcode.py` (both unmounted).
8. **The suspected "provider vs URL key" bug in the YouTube-off switch does NOT exist** — the switch was implemented correctly (URL-substring match in both query and loop); the real gap was the manual endpoint bypassing it (finding 2).
9. **"Run Little Girl" mystery explained:** resolve → permanent-dead → auto-delete worked correctly, but the UI showed a generic error, left the ghost card, and the subsequent skip click hit a video that no longer existed ("Video not found"). UX lying by omission, not data corruption.

Lock-discipline verification (per Colby's constraint): rebuild is a server-side write that serves nothing; `build_channel_schedule` enforces separation in its own source query (adult ⇒ `locked=1` only; main ⇒ `locked=0` only); Xtream separately maps public→main / private→adult at serve time. Bulk rebuild cannot cross-contaminate. New rule adopted: dashboard job summaries are **counts only, never titles** (Settings page is visible to locked sessions).

---

## WHAT SHIPPED (Phase B — one file at a time, all full-file deliveries)

1. **services/resolver.py** — `resolve_batch()` gained `channel_ids` + `should_abort` (single batch implementation for scheduler AND button); `resolve_video_for_tv()` gained `force` (bypasses cache + legacy failed-skip; never bypasses back-off/cookie-stale guards); `warm_tv_cache()` and `purge_dead_videos()` deleted; all warm-pass doc drift corrected.
2. **routers/resolve.py** — `/batch` delegates to the service (expired pass gone; Stop button works via abort callback; toast now itemizes results). Single-video resolve returns honest outcomes: **410** "permanently unavailable — removed from catalog" (incl. ghost-card pre-check), **503** "stays in catalog, will retry" + recorded error. `force` reaches the TV path. `/reset-failed` and `/purge-dead` endpoints removed. NEW: `GET/POST /resolve/youtube-background` (the long-planned switch endpoints).
3. **tasks/scheduler.py** — TWO NEW JOBS: `epg_rebuild_job` (6 h, fires at startup — restarts self-heal stale schedules) and `xmltv_refresh_job` (2 h). Job-run registry: every job wrapped by `@_track_job` recording start/end/duration/result/error/counts; every job returns a counts-only plain-English summary. `get_scheduler_status()` + `run_job_now()` power the dashboard.
4. **routers/health.py** — NEW `GET /health/scheduler` (dashboard feed) and `POST /health/scheduler/run/{job_id}` (Run Now). Placed in health to avoid touching main.py.
5. **routers/library.py** — NEW `_delete_video_children()` helper (Favorite/WatchHistory/Watchlist explicit deletes — see finding 4); wired into purge-missing-files AND the 🗑️ delete endpoint. Purge-missing gained a two-phase scan + **empty-mount guard**: if 100 % of local files look missing, abort with a plain-English message instead of mass-deleting over an unmounted volume. Module docstring endpoint list now includes purge-missing (was absent — the doc gap that made the button a mystery).
6. **routers/feed.py** — NEW `?problems=true` filter param (`resolution_error IS NOT NULL`) on `/feed`, applied to both query and count.
7. **templates/index.html** — 📅 Background Scheduler card on Settings (9 jobs: status dot incl. pulsing running-state, last run + duration, result line, next-run countdown, ▶ Run Now per job; 10 s auto-refresh while visible; YouTube background toggle wired to the new endpoints). ⚠️ Problems option in the Catalog status filter; amber "problem" badge with error-text tooltip on pending-with-error cards. Ghost-card fix: 410 removes the card immediately. ♻️ Reset Failed + 💀 Purge Dead Links buttons and handlers removed. Resolve-25 tooltip states the YouTube on-demand policy.

---

## LIVE FILE HASHES AFTER THIS SESSION (source of truth)

| File | Container path | New SHA-256 | Verified in container |
| --- | --- | --- | --- |
| resolver.py | /app/app/services/resolver.py | 82cb02949890635922d8040567ccb9a99d4552328606eb19fdbf50d7448e6599 | ✅ |
| resolve.py | /app/app/routers/resolve.py | 7e2f9f2fa0dda5f0aa176050ed86b17d771801ecb93599120e7a9a96f642ca08 | ✅ |
| scheduler.py | /app/app/tasks/scheduler.py | 1cc5f95c8e035ca871b3ed170600d0f1f7298dd1f4d58a67179c57b839d92252 | ✅ |
| health.py | /app/app/routers/health.py | b0655312d4f27920ba0e49052aee4b95ac15ffad19a493043d50c2c092084f2e | ✅ |
| library.py | /app/app/routers/library.py | dcc3058815516b029c346fc69a5328a2c9e0e5e392b944895828cb953e65c884 | ✅ |
| index.html | /app/app/templates/index.html | 9c565174f74cc8a6a2e43f37c64ab079a6740919bbcb7a7267552d53503c1066 | verify on deploy |
| feed.py | /app/app/routers/feed.py | 95b84bb66dad53d77111ad5a1c4e85bb96b8d0b31d2ca02930c8cdde132b85bb | verify on deploy |

In-session hardware verification: `GET /health/scheduler` returned all 9 jobs with live data (24/24 Live TV channels online, real durations, running-state flag mid-scrape). Browser-side panel, Problems view, ghost-card removal, Run Now buttons, and YouTube toggle NOT yet eyeballed in the browser — Session 64 item.

---

## KEY LEARNINGS THIS SESSION

- **The zombie-status pattern:** when a status stops being written, every reader of that status becomes a silent no-op. Removing a status-writer requires grepping for ALL readers (buttons, endpoints, filters, badges) in the same change.
- **Router endpoints must delegate, never duplicate.** `/resolve/batch` carried its own copy of batch logic and drifted for ~7 sessions, silently missing every guard added to the service. One implementation, many callers.
- **SQLite CASCADE is decorative without `PRAGMA foreign_keys=ON`.** Every delete path must clean child rows explicitly — and rowid reuse turns orphans into future data corruption, not just clutter.
- **"Called by the scheduler every N hours" in a docstring proves nothing.** Two functions claimed schedules that were never registered. Verify claims against actual `add_job` calls.
- **Destructive filesystem-vs-DB reconciliation needs an all-missing guard** — "everything is gone" almost always means the mount is gone, not the files.
- **Surfaces visible to locked sessions get counts, never titles** (scheduler dashboard rule).
- **UX honesty about automatic actions:** when the backend auto-deletes something, the UI must say so and remove the artifact — a correct backend plus a silent UI still reads as a bug to the operator.

---

# NEXT SESSIONS — QUEUE

1. **Hardware-verify progressive YouTube streaming** (carried from S62, still #1): cold-play a SHORT then a LONG (1 h+) YouTube video in TiviMate; log should show `STREAM PIPE … first bytes ready`; confirm no orphaned ffmpeg after stop. If `falling back to full-download remux`, paste the ffmpeg error.
2. **Browser-verify Session 63 UI:** scheduler panel populates + auto-refreshes, Run Now works, YouTube toggle flips, ⚠️ Problems filter lists error videos with amber badges, resolving a permanently-dead video removes the card, Purge Missing Files behaves (and its abort guard if ever unmounted).
3. **Deploy-hash confirm index.html + feed.py**, then complete the deferred host + GitHub hash legs (steps 2 & 3 from Phase 0) — this session's three-way check covered container + project only.
4. **Dead-file cleanup (now fully inventoried):** delete `/app/app/scheduler.py`, `routers/feed_work.py`, `routers/transcode.py` from host + repo; check Dockerfile COPY behavior first; `docker compose up -d`. Also the stray host-side index.html (pending step 2 output).
5. **Extend child-row cleanup to the resolver's `_delete_video()`** — it cleans Favorite only; auto-delete/dedup/purge paths still orphan WatchHistory/Watchlist rows. Either add `_delete_video_children`-style cleanup there or enable `PRAGMA foreign_keys=ON` app-wide (bigger change, test carefully). One-time orphan sweep of existing rows while at it.
6. **Legacy `failed` rows:** now visible in ⚠️ Problems — review and 🚫 individually, or decide on a one-time cleanup.
7. **6-hour YouTube temp cache (40 GB cap)** — seek support for the pipe path (tee to disk while piping). Blocked behind queue #1.
8. **Catalog integration decision** (downloaded Reddit files in the web Catalog) — carried from S62.
9. **Carried opens:** public-login lock-discipline eyeball; v.redd.it playback test in TiviMate; reconcile job; "Saved to NAS" badge/count split on source cards; WatchDawg pseudo-channels; PIN-lock web-UI removal (keep source lock/unlock); Reddit cookie refresh instructions in web UI; clone count.

**Housekeeping:** refresh the Claude project files — upload this summary plus the seven Session 63 files (resolver.py, resolve.py, tasks/scheduler.py, health.py, library.py, feed.py, index.html). Also still stale/missing in the project: main.py, models.py, database.py, proxy.py, web_ui.py, epg.py, tasks/pseudo_scheduler.py, providers/base.py, providers/playlist.py.
