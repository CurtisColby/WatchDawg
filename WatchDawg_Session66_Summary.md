# WatchDawg — Session 66 Summary

**Session Dates:** July 12–13, 2026 (single continuous session across two days)

**Baseline:** Session 65 state (resolver.py YouTube cookie-burn guards deployed via bind-mount, container restart pending; GitHub sync pending; Vimeo IP-block active).

**End State:** Batch Resolve slowed to a 4-hour cadence with no startup fire; Vimeo IP-block confirmed LIFTED; the "provider blocked" alarms that persisted after the slowdown were root-caused as head-of-queue poisoning by individually-dead Vimeo videos — NOT an active block; queue cleared via one-off sweep script; permanent "Purge Vimeo 403s" endpoint + Maintenance button shipped. All delivered files anchored three ways. Session 65's pending resolver.py module reload was completed implicitly by this session's first restart.

---

## PART 1 — Batch Resolve slowdown (scheduler.py)

**Trigger:** Vimeo 403s on every background resolve; operating assumption at the time was an active IP block from hitting Vimeo too hard.

**Change (scheduler.py, code comments labeled "Session 65"):**
- New `RESOLVE_INTERVAL_HOURS = 4` constant. Batch Resolve decoupled from the 30-minute scrape tick and given its own `IntervalTrigger(hours=4)`.
- **No `next_run_time` on the resolve job** — it no longer fires at container startup. Every restart used to greet Vimeo with a fresh batch. First run is at +4h; dashboard Run Now is the escape hatch.
- Limit stays 25/tick → ceiling dropped from ~1,200 Vimeo hits/day to ~150/day.
- Scrape untouched: still every 30 min with startup fire. New posts land as pending promptly; they just resolve on the slower schedule. YouTube unaffected (resolves on-demand at play).
- Module docstring, batch-size rationale, and startup log line updated to match (log now reports scrape and resolve intervals separately).

**Verified:** container restart → startup scrape fired, no startup Batch Resolve; dashboard showed next resolve ~4h out.

---

## PART 2 — The "provider blocked" false alarm (the big diagnostic win)

**Symptom:** Even after the slowdown, every Batch Resolve tick showed `resolved 0/25, 5 transient failures, stopped early (provider blocked)`.

**Log audit findings:**
1. **The same video id (4267, a Vimeo video) appeared at the head of BOTH breaker traces, hours apart.** The batch query orders by `reddit_score DESC` — deterministic — and transient failures stay pending. So the same ~5 dead videos led every batch, tripped the 5-consecutive-failure circuit breaker, and starved everything behind them.
2. **In the same second as a batch 403, the thumbnail backfill successfully pulled Vimeo metadata** for another video — proof Vimeo was NOT blocking the IP wholesale.

**Confirmation test (now the standard Vimeo health check):**
```
docker exec watchdawg-backend yt-dlp --simulate --cookies /config/cookies.txt "https://vimeo.com/76979871"
```
Clean extraction → block lifted. **The Session 59/65 slow-drip changes worked; Vimeo forgave the IP.**

**Root cause named: HEAD-OF-QUEUE POISONING.** A handful of individually-dead Vimeo videos (403 = private, embed-restricted, or deleted) camp at the front of the score-ordered queue forever because their 403s are classified transient. The circuit breaker cannot distinguish "provider is blocked" from "the first five videos are individually dead." The dashboard's "provider blocked" line is therefore ambiguous — see decision procedure below.

---

## PART 3 — Queue cleanup + permanent fix

**One-off sweep (executed):** `sweep_vimeo_403.py` — dry-run-by-default script run inside the container (`docker exec -e PYTHONPATH=/app …`). Targets pending + Vimeo + error-contains-403 ONLY. Purge mode mirrors POST /skip exactly: HMAC hash → encrypted SkipListEntry (prevents re-import) → ORM delete (Session 64 cascade cleans favorites/watch_history/watchlist) → single commit. Dry run found exactly 1 remaining poison video (id 4267; Colby had already cleared 4 via the ⚠️ Problems view); purge removed it cleanly. **Script's permanent home: copy to `~/watchdawg-backend/data/` so it survives container recreation** (was in /tmp during the session — re-copy if needed).

**Permanent feature (shipped):**
- **Backend:** `POST /resolve/purge-vimeo-403` in resolve.py, with `?dry_run=true` count-only mode. Same narrow selection criteria; same skip-list-mirror logic; counts-only responses (lock discipline — surfaces on Settings). Declared with the static routes above `/{video_id}`; route order machine-verified (14 static routes above first dynamic at line 634).
- **UI:** ☠️ **Purge Vimeo 403s** button in the Maintenance card. Click flow: dry-run count first → if 0, "queue is clean" toast → else confirmBox showing exact count with the active-block warning → purge → result toast. The dry-run-then-confirm gate is baked in; cannot be fat-fingered.
- Verified working in browser post-deploy ("queue is clean" path exercised).

---

## OPERATOR DECISION PROCEDURE — "provider blocked" on the dashboard

1. Run the Vimeo health check one-liner (above).
2. **If it FAILS (403):** real IP block. Do NOT purge — the videos aren't dead, Vimeo is mad. Wait it out; the 4-hour/no-startup-fire cadence is the cool-down.
3. **If it SUCCEEDS but the breaker still trips every tick:** head-of-queue poisoning. Click ☠️ Purge Vimeo 403s in Maintenance (or run the sweep script). May need a second round if more dead videos surface at the new head.

---

## LIVE FILE HASHES AFTER THIS SESSION (source of truth, all three-way verified)

| File | Container path | SHA-256 | GitHub commit |
| --- | --- | --- | --- |
| scheduler.py | /app/app/tasks/scheduler.py | 6784db9f321a1a2d55f48bdcb2677c4109f8f15a34204620b94197beab39f2ee | 627c0f4 |
| resolve.py | /app/app/routers/resolve.py | 0e8317c562aa47bbe2b82a9ff4a202ce728b0f43a5fdeb9c8ba6683694112f99 | 3f36b48 |
| index.html | /app/app/templates/index.html | bdccc7541d16222811f62fda6e70e25eb42d912002bb8d5cd8bbaab103709431 | 3f36b48 |
| resolver.py | /app/app/services/resolver.py | f39c395667198bd402ed69406517b277afa7100e9b0d2e55302d382bb3524e39 | (Session 65 patch — confirmed LOADED in container this session; GitHub leg status unverified, carry forward) |

Session-64 anchors for models.py, health.py, library.py, feed.py unchanged.

---

## KEY LEARNINGS THIS SESSION

- **"Provider blocked" on the dashboard is ambiguous.** The circuit breaker trips on 5 consecutive transient failures — which an active IP block AND 5 dead videos at the head of a deterministic queue produce identically. Always disambiguate with the one-line yt-dlp health check before acting.
- **Look for repeated video IDs across breaker traces.** The same id leading multiple failed batches hours apart is the head-of-queue-poisoning signature.
- **A success line adjacent to the failures is diagnostic gold.** One successful Vimeo metadata call in the same minute as the 403s disproved the wholesale-block theory instantly.
- **"Transient" classification + deterministic queue order = permanent head-of-line blocking.** Session 59's poison-write fix (never mark failed) was correct but created this dual: videos that will never resolve now stay pending forever and, if high-scored, block the queue. The attempts-counter feature (queued) resolves the tension properly.
- **Vimeo 403 semantics are context-dependent:** during an active block it means "you are blocked" (do NOT delete); when Vimeo is otherwise healthy it means "this video is restricted/dead" (safe to purge). Never auto-classify Vimeo 403 as permanent — a future block would mass-delete healthy videos.
- **File-transfer ritual (cost three round-trips this session):** files Claude delivers exist only as chat downloads until explicitly moved. The ritual is always: (1) download from chat, (2) `scp` to PlexServer, (3) sha256sum-verify BEFORE cp/deploy. Deploy commands in chat are for AFTER the file physically arrives.
- **Running in-container scripts:** `docker exec -e PYTHONPATH=/app watchdawg-backend python /path/script.py`. Neither running from /tmp nor `-w /app` puts the app package on Python's import path — PYTHONPATH does.
- **Pre-deploy container-hash checks earn their keep:** every deploy this session was gated on the container still matching the last anchor; one gate caught a not-yet-arrived file before it could half-deploy.

---

# NEXT SESSIONS — QUEUE

1. **Attempts-counter feature (the durable fix for head-of-queue poisoning):** one new column on videos; each failed resolve increments; past threshold N the video is deprioritized to the back of the batch queue and auto-badged in ⚠️ Problems. Kills this failure mode permanently without deleting anything. Design decision: threshold value, and whether deprioritized videos are eventually retried.
2. **Verify resolver.py GitHub leg** (Session 65 hash f39c3956… — container/host confirmed, repo leg never explicitly verified).
3. **First healthy Batch Resolve result line** — confirm a real `resolved N/25` now that the queue is clean (Run Now or next 4-hour tick).
4. **Copy sweep_vimeo_403.py to ~/watchdawg-backend/data/** if not yet done (it was in /tmp — gone on container recreation; the button makes it optional but it's the fallback).
5. **Global Problems view across all sources** with bulk-select purge (nice-to-have now that the 403 button exists; deprioritized).
6. **Carried from earlier sessions:** ffmpeg thumbnail permanent-failure marker (same Reddit/local files retrying every tick); YouTube 6-hour temp cache; catalog integration decision for downloaded Reddit files; channel rename feature ("Local"/"Online" prefixes, name-only PATCH); PIN lock removal from web UI (keep source lock/unlock); Reddit cookie refresh UI instructions; dead-file cleanup execution (scheduler.py at /app/app/, feed_work.py, transcode.py, stale index.html copies); browser eyeball of remaining Session 63 UI items.
