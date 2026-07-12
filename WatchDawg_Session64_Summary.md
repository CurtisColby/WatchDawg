# WatchDawg — Session 64 Summary

**Session Date:** July 11, 2026

**Baseline:** Session 63 state (audit fixes + scheduler dashboard + Problems view shipped; progressive YouTube streaming and Session 63 UI not yet hardware-verified).

**End State:** Both carried verifications passed, three production bugs root-caused and fixed, dead-file cleanup completed, all shipped files anchored three-ways (container + host + GitHub). Backend verified healthy at session close.

---

## CARRIED VERIFICATIONS — BOTH PASSED

1. **Progressive YouTube streaming (S62 queue #1):** Colby verified cold YouTube play working in TiviMate AND the OwnTV client. Closed.
2. **Session 63 UI (queue #2):** Scheduler dashboard, Run Now, Problems view all verified working in browser. The dashboard immediately earned its keep by surfacing two live production bugs (below).

---

## BUG 1 — Batch Resolve crashed every tick (IntegrityError)

**Symptom (surfaced by the new scheduler dashboard):** Batch Resolve "took 1.2s" every tick with
`(sqlite3.IntegrityError) NOT NULL constraint failed: watch_history.video_id [UPDATE watch_history SET video_id=NULL WHERE id=95]`.

**Root cause:** models.py's Video child relationships (`favorite`, `watch_history`, `watchlist_entry`) had **no cascade configured**. SQLAlchemy's default when deleting a parent with a loaded child relationship is to NULL the child's FK — not delete the child. `watch_history.video_id` is NOT NULL, so the nullify crashed, the ROLLBACK killed the entire batch (discarding any legitimate resolves earlier in it), and the same watched-but-dead pending video (watch_history id=95) re-triggered the crash every tick. 87 pending videos had watch_history rows — 87 landmines.

**Fix (models.py, hash `0e3d1feb…`):** `cascade="all, delete-orphan"` on all three child relationships. ORM-level, so it works regardless of SQLite's unenforced `ondelete=CASCADE` (PRAGMA foreign_keys is never set). One implementation, every caller: resolver auto-delete, dedup, purge, and library deletes all inherit it. AsyncSession.delete() pre-loads cascading relationships before flush, so async paths are safe. No schema migration.

**Verified:** Run Now on Batch Resolve → clean completion, **10 dead videos removed** in one pass, no IntegrityError.

**One-time orphan sweep result: 0 orphans in all three child tables.** Satisfying footnote: the NOT NULL constraint meant every attempted nullify crashed and rolled back instead of stranding a row — the bug was accidentally preventing the very orphans the Session 63 audit feared. The rowid-reuse hazard never materialized.

---

## BUG 2 — "YouTube background resolving: unknown" (route-order 422)

**Symptom:** Scheduler dashboard header stuck on "unknown"; toggle button hidden.

**Root cause (classic FastAPI route-ordering bug):** in resolve.py, `@router.get("/{video_id}")` (video_id: int) was declared at line 200 — BEFORE the static GET routes (`/youtube-pause` 586, `/cookie-stale` 632, `/youtube-background` 667). Starlette matches in declaration order: `GET /resolve/youtube-background` was captured by `/{video_id}`, failed int validation → 422 → the frontend's catch printed "unknown". **The toggle itself worked** because POST routes fall through (`/{video_id}` is GET-only) — only the GET readouts were broken. Same silent capture affected GET `/youtube-pause` and GET `/cookie-stale`.

**Fix (resolve.py, hash `6af72360…`):** pure declaration reordering — all 13 static routes moved above the four dynamic `/{video_id}` routes, with a "ROUTE ORDER IS LOAD-BEARING" comment block between them so it can't regress. Zero lines of logic changed (verified: 0 lines lost, only the comment added).

**Verified:** `curl http://192.168.50.42:6868/resolve/youtube-background` → `{"youtube_background_resolve_enabled":false}` (was 422). Backend port note: **6868**, not 8000.

---

## BUG 3 — Thumbnail retry-forever loop (and it wasn't corruption)

**Symptom:** the same handful of Reddit files logged `ffmpeg failed` every 30-minute thumbnail tick, forever.

**Root cause (diagnostic ffmpeg run proved the files healthy):** `FFMPEG_GRAB_SECOND = 5` — the pass seeks to 5s before grabbing a frame, but many Reddit clips are **shorter than 5 seconds**. Seek past EOF → zero frames encoded → error → no thumbnail → retried next tick. No failure memory existed. (Session 58's outcome-aware fix covered only the yt-dlp backfill pass, not the ffmpeg local-file pass.)

**Fix (library.py, hash `df546358…`, base `dcc305…`):**
1. **Two-attempt grab:** `-ss 5` first (better mid-clip thumbnails for normal videos), **frame-0 retry** on failure — short clips get real thumbnails instead of being skipped.
2. **Thumbfail marker** (`file.mp4.watchdawg_thumbfail.txt`) written only when BOTH attempts fail; contains the ffmpeg error + timestamp; both passes skip marked files. Delete the marker to force a retry — no endpoint needed. `.txt` extension keeps markers invisible to the library scan.
3. **Zero-byte guard:** empty partial .jpg output is removed so it can't masquerade as a thumbnail.
4. **Lifecycle:** the file-delete endpoint removes the marker along with the file and thumbnail sidecar.

**Verified:** deployed via freshness gate; thumbnail pass generating sidecars post-deploy; Colby confirmed done after additional Run Now passes and pushed to GitHub. (Per-file frame-0 vs. marker verdict on the specific stuck Reddit clips not pasted into session record — spot-check Files on Disk next session if thumbnails look absent.)

---

## DEAD-FILE CLEANUP (queue #4) — COMPLETED

Recon confirmed: docker-compose bind-mounts `./app:/app/app` (image's baked COPY is shadowed — host deletion propagates instantly, no rebuild), and **nothing imports** feed_work or transcode (grep hits were comments and Vimeo CDN URL patterns only).

Deleted from host (and thus container): `app/scheduler.py` (pre-S59 stray; real one is `app/tasks/scheduler.py`), `app/routers/feed_work.py`, `app/routers/transcode.py`, plus **two** stray index.html copies (`~/watchdawg-backend/index.html` and `~/watchdawg-backend/templates/index.html` — one more than inventoried; the real file is `app/templates/index.html`, hash-confirmed `9c5651…` before deletion).

Deleted from repo (commit `9f763ae`, −2,939 lines): `backend/app/routers/feed_work.py`, `backend/app/routers/transcode.py`, `backend/index.html` (repo-side stray found via `git ls-tree`).

Post-cleanup: container ls confirms all gone; `/health` healthy.

---

## REPO LAYOUT DISCOVERED (three-way hash checks now fully specified)

GitHub sync repo paths are **`backend/app/...`** — not `app/...`. E.g. `backend/app/models.py`, `backend/app/routers/resolve.py`, `backend/app/templates/index.html`. GitHub leg command pattern:
`cd ~/watchdawg-repo-sync && git show origin/main:backend/app/<path> | sha256sum`
(Raw fetch from github.com fails — repo is private. `e3b0c44…` output from a failed `git show` is the empty-string hash, not a real result.)

---

## LIVE FILE HASHES AFTER THIS SESSION (source of truth)

| File | Container path | New SHA-256 | Container | Host | GitHub |
| --- | --- | --- | --- | --- | --- |
| models.py | /app/app/models.py | 0e3d1febeac1b314c456fbd10c8b28ed69f855c46835c45c4f9df72688ee818c | ✅ | ✅ | ✅ |
| resolve.py | /app/app/routers/resolve.py | 6af723604a451300801cfa796d5d3789994fcffbe8565aacfba2b4781a4072e5 | ✅ | ✅ | ✅ |
| library.py | /app/app/routers/library.py | df546358a4209e74c623b648ac638176e7cceb0317f67290cafb7511a2569e2e | ✅ | ✅ | ✅ (pushed by Colby) |

Unchanged this session but relevant: health.py live = `b06553…` (matches S63 summary; **project copy is stale**).

Session-end health check: `{"status":"healthy","database":"connected"}`, YouTube cookies `ok` with recent successful resolve, no pauses active, background resolve OFF.

---

## KEY LEARNINGS THIS SESSION

- **A dashboard that surfaces errors pays for itself immediately.** Both production bugs this session were invisible in normal operation and obvious the moment the Session 63 scheduler panel rendered its first error string.
- **Missing ORM cascade ≠ just orphans — it can be a crash.** SQLAlchemy's default parent-delete behavior is to NULL the child FK; with a NOT NULL constraint that's an IntegrityError that rolls back the whole transaction. The constraint accidentally acted as an orphan-prevention tripwire.
- **A rolled-back batch is worse than a failed item.** One poisoned video discarded every legitimate resolve in the same batch, every tick. Failure isolation matters in batch loops.
- **Static routes before dynamic routes, always.** FastAPI matches in declaration order; `/{video_id}` declared first silently eats every single-segment GET under the prefix as a 422. POSTs falling through masked the bug — "the toggle works but the readout doesn't" is the signature.
- **Diagnose before building the workaround.** The planned fix was a skip-marker for "corrupt" files; one diagnostic ffmpeg run showed the files were healthy and the seek point was the bug. The real fix (frame-0 retry) produces thumbnails where the workaround would have produced skips.
- **The bind mount means deploys are copy + restart and deletions propagate instantly** — but the image still carries a stale baked copy of app/ (shadowed at runtime; only matters if the mount is ever removed).
- **Backend port is 6868** (not 8000) for curl checks from the host.

---

# NEXT SESSIONS — QUEUE

1. **6-hour YouTube temp cache (40 GB cap)** — seek support for the progressive pipe path (tee to disk while piping). Now UNBLOCKED (progressive streaming hardware-verified). Full-session effort; make it a session headliner.
2. **Legacy `failed` rows decision** — visible in ⚠️ Problems; review and 🚫 individually or one-time cleanup.
3. **Spot-check the previously-stuck Reddit clips** in Files on Disk — confirm frame-0 thumbnails appeared (or read any .watchdawg_thumbfail.txt markers for the genuinely unreadable ones).
4. **Channel rename feature (scoped this session, parked):** new `PATCH /channel/{id}/name` in channel.py + ✏️ rename control on source cards in index.html. Motivation: "Local —"/"Online —" name prefixes so TiviMate shows what plays from disk vs. resolves live. Design note: a channel-level prefix reflects intent, not per-video truth (disk-first playback means downloaded videos in "Online" channels still play locally). Future idea logged: automated 💾 marker on downloaded videos in Xtream titles for per-video truth.
5. **Catalog integration decision** (downloaded Reddit files in the web Catalog) — carried from S62.
6. **Carried opens:** public-login lock-discipline eyeball; v.redd.it playback test in TiviMate; reconcile job; "Saved to NAS" badge/count split on source cards; WatchDawg pseudo-channels; PIN-lock web-UI removal (keep source lock/unlock); Reddit cookie refresh instructions in web UI; clone count; scraper insert-before-download phantom pattern (S61).

**Housekeeping:** refresh the Claude project files — upload this summary plus the three Session 64 files (models.py, resolve.py, library.py). Still stale/missing in the project: main.py, database.py, proxy.py, web_ui.py, epg.py, tasks/pseudo_scheduler.py, providers/base.py, providers/playlist.py, **health.py** (project copy ≠ live `b06553…`).
