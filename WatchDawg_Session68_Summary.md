# WatchDawg — Session 68 Summary

**Session Date:** July 22, 2026

**Baseline:** Session 67 state (sub-request 404 transient guard live at resolver.py 600456bf…; Batch Resolve on 4-hour cadence; Vimeo fallback ladder gated on attempts-counter data).

**End State:** Root-caused the total Vimeo resolve outage as a VIMEO-SIDE PLATFORM CHANGE (anonymous extraction killed 2026-07-20, account cookies now mandatory); worked around it with nightly yt-dlp + Vimeo account cookies + per-provider cookie routing in the resolver; discovered and began clearing a deep "dead cohort" of long-gone Vimeo videos via a new verify-then-purge sweep (script + web UI two-button flow); shipped the Vimeo cookie-stale pause with a Settings green/red light. First healthy Vimeo resolve since July 20 confirmed in production ("Resolved 1 of 25"). Operator is cycling batch→verify→purge over the coming days to chew through the dead backlog. **GitHub sync pending at close — see PENDING section.**

---

## PART 1 — The outage root cause (the big finding)

**Symptom:** Every Vimeo resolve failing; same 5 videos leading every batch; breaker tripping every tick. Operator unsure whether videos were dead, cookies were bad, or bot-protection was active.

**Diagnostic chain (each step changed the answer):**
1. Health-check one-liner → **401 Unauthorized on macos API** — NOT the familiar 403/404 patterns, and it hit the known-good canary video, proving infrastructure failure, not video death.
2. Cookie-less test → same 401. Cookies ruled out (again).
3. Web research → **yt-dlp issue #17271 (opened 2026-07-20): Vimeo disabled anonymous OAuth token fetch platform-wide.** The macos client was the ONLY anonymous path; android/ios are cache-only; web requires login. Zero working anonymous paths remained. Reproduced globally across IPs/versions — nothing WatchDawg-specific.
4. Upstream fix (PR #17272, merged 2026-07-20) is **master/nightly only** — stable 2026.07.04 (installed) remains broken. And the "fix" doesn't restore anonymous access (impossible); it makes yt-dlp fail cleanly and demand credentials.

**Bottom line: since 2026-07-20, Vimeo extraction REQUIRES a logged-in account. Permanently, as far as anyone knows. Every WatchDawg Vimeo resolve failed from that date until this session's fix.**

**Confusion explained retroactively:** during the outage, dead videos and healthy videos failed identically, making triage impossible. That ambiguity was the entire mystery.

---

## PART 2 — The workaround chain (all three legs required)

1. **Nightly yt-dlp** (`pip install -U --pre "yt-dlp[default]"` → 2026.07.21.234255.dev0) + `--rm-cache-dir` (stale OAuth token cache from the broken era caused a misleading intermediate failure — cache clear after Vimeo-related yt-dlp updates is now standard).
2. **Vimeo account cookies**: operator's free Vimeo account, exported via browser addon. **First export failed** ("only works when logged-in" persisted) — the export was missing the HttpOnly session cookie. **Fresh export with the addon on a freshly-loaded logged-in vimeo.com tab succeeded** → full clean extraction (`Downloading 1 format(s): http-720p`).
3. **Per-provider cookie routing** (deployed, Part 3).

**Canary change:** the classic test video **76979871 is broken on Vimeo's own site** ("This video is processing…") — retired. **New canary: 129731718** (Session 67's live-proof video). A personally-owned video would be more durable — queue note.

---

## PART 3 — Deploy #1: per-provider cookie routing (3 files)

- **config.py**: new `vimeo_cookies_path` = `/config/vimeo.com_cookies.txt` — deliberately the browser addon's exact export filename so refresh = export → copy, no rename ever.
- **resolver.py**: `_is_vimeo_source()` helper + `_cookies_path_for(url)` — Vimeo URLs get the Vimeo cookie file, everything else keeps `/config/cookies.txt` (domain-scoped jars make fallback harmless). Warn-once log if the Vimeo file is missing. All three extraction call sites (resolve, TV, thumbnail) switched.
- **docker-compose.yml**: new per-file mount for `vimeo.com_cookies.txt` (host file MUST exist before `up -d` or Docker creates an empty directory).

**Deploy incidents (lessons):**
- First attempt skipped the container recreation → mount absent, old code loaded. Recreation (`up -d`) is required for volume changes (Session 65 lesson, re-confirmed).
- **Recreation reverted yt-dlp to the image's baked-in build** — and mid-session pip surgery revealed the image bakes a *May nightly* (2026.5.24.dev0) with corrupted metadata (`version: None` pip crash), which the startup auto-updater then moves to stable. `pip install --force-reinstall --pre "yt-dlp[default]"` repaired the environment cleanly (verified CLI + module = 2026.07.21.234255).
- **Found the 24-hour yt-dlp auto-updater**: it's a scheduler job ("yt-dlp auto-update interval: 24 hours (runs at startup)") — this explains stable 2026.7.4 appearing after recreation. A pip-based updater won't downgrade the nightly (version-higher), but the job's code should be read next session. **Standing rule until the fix ships in stable: after ANY container recreation, force-reinstall nightly + rm-cache-dir.**

---

## PART 4 — The dead cohort + verify-then-purge sweep

With auth healthy, the head-of-queue failures resolved into a **contiguous block of genuinely dead videos** (DB ids ~5985–6010+, old scrape era, channel 2 "EROTICA"): browser 404, CLI 404, app 404 — triple-confirmed. The existing ☠️ Purge Vimeo 403s button can't catch them (they store 404s, not 403s).

**New tool: `data/sweep_vimeo_404.py`** (permanent home survives recreation; hash ab3d8efb…). Session 66's 403 sweep pattern with a critical safety upgrade honoring the Session 67 lesson (Vimeo intermittently 404s LIVE videos — a stored 404 is not a verdict):
- **Canary first** — canary failure = auth/extractor broken → ABORT with zero verdicts (a global outage must never read as mass death).
- **Live re-extraction of every candidate** with Vimeo cookies, randomized 5–10s pacing. Verdicts: fresh-404 → purge candidate; alive → never touched; anything else → unverified, never touched.
- Dry-run default (network reads, zero DB writes) prints the purge list with titles for operator review; PURGE re-verifies, then mirrors POST /skip exactly (encrypted skip-list entry + ORM delete + Session 64 cascade), single transaction.
- **First production run: 7/7 candidates confirmed dead and purged cleanly** (full SQL trace reviewed and verified correct: per-video skip-list INSERT + cascade child loads + DELETE, one COMMIT).

**First healthy batch line since July 20: "Resolved 1 of 25"** — proof the fleet works; the head is just a graveyard. Operator workflow for the coming days: **Resolve 25 Pending → Verify → review → Purge → repeat** until batches post healthy numbers.

**Design decision (operator-ratified): NO auto-purge after batches.** Deletion stays an operator decision (Session 67 rule); an auto-purge minutes after first failure could double-404 an unlucky live video into permanent skip-listing. The attempts-counter is the correct automation (deprioritize, don't delete).

---

## PART 5 — Deploy #2: cookie-stale pause + web UI (4 files)

- **resolver.py**: Vimeo cookie-stale pause mirroring YouTube's — detects "only works when logged-in" **gated on Vimeo source URLs** (the message contains `--cookies-from-browser`, which overlaps YouTube's COOKIE_STALE_KEYWORDS; URL gating keeps the providers' states independent — `_record_youtube_result`'s own URL gate prevents the reverse cross-trip). Batch skips Vimeo while stale (`skipped_vimeo_stale` in summary + log); manual Resolve still attempts (deliberate: it's the "test my fresh cookie" button); self-clears on first Vimeo success. Plus the **verify-then-purge job engine**: module-level job state (counts-only status per lock discipline; confirmed-dead ids module-private), canary + process-pool live checks with 60s timeouts + polite pacing, purge guarded by state=done + 30-minute freshness window + per-row re-checks, own DB session.
- **resolve.py**: `POST /vimeo-404-verify` (idempotent start), `GET /vimeo-404-status` (poll), `POST /vimeo-404-purge` (409 unless ready). Declared above `/{video_id}`; **route order machine-verified: 17 static routes above first dynamic, none after.**
- **health.py**: `vimeo_cookies` block (state ok/stale/missing + file_present) via import-inside pattern.
- **index.html**: 🎬 **Vimeo Cookies card** — green/red dot, full refresh instructions (export from logged-in vimeo.com tab → `cp ~/Downloads/vimeo.com_cookies.txt ~/watchdawg-backend/config/` → no restart; hand-verify one-liner with the new canary). Maintenance: 🔍 **Verify & Purge Vimeo 404s** — start job → toast → 3s polling with live (n/total) progress on the button → confirmBox with confirmed-dead/healthy/unverified counts → purge → toast. Wired into refreshHealth (and its failure path).

Deployed via cp + `docker compose restart` (code-only — nightly yt-dlp preserved).

---

## LIVE FILE HASHES AFTER THIS SESSION (container = host verified at deploy)

| File | Container path | SHA-256 | GitHub |
| --- | --- | --- | --- |
| resolver.py | /app/app/services/resolver.py | b1916ea8e80bafbf6b4b8f8604315f606206cbdd1422b996cb7e4c9db4d78260 | **PENDING** |
| resolve.py | /app/app/routers/resolve.py | fecdab3e301fd9cda2657987241ee90abe702f07923d0759ddadc594eeef0b88 | **PENDING** |
| health.py | /app/app/routers/health.py | 1b451769ad1f292d54cd3287140d11b57434ae2b9c04fdda7018ffd4859f8f24 | **PENDING** |
| index.html | /app/app/templates/index.html | 1803b03a1a70f1d1b2a373fd48070758bb3a755c09b866e3a9f17b36651190fc | **PENDING** |
| config.py | /app/app/config.py | 8092d771b15bb380545543e4d20cbe1159765e808b4a41312bf1ebd2c64b47ca | **PENDING** |
| docker-compose.yml | (host only) ~/watchdawg-backend/docker-compose.yml | 9fe3a60bd4749ccb40daa844f9300df80b3b71c059ec2aa808f15fc836b9a545 | **PENDING** |
| sweep_vimeo_404.py | /app/data/sweep_vimeo_404.py | ab3d8efb6925ddd9f03b1d06717a3da900644328cb69df0b73cdad0ef13cef9a | **PENDING** |

Intermediate deploy-#1 hashes superseded same-session: resolver.py 3ed8ebfe…, resolve.py 0e8317c5… (S66), health.py b0655312…, index.html bdccc754… (S66).

**Stale-project-file hazard, three strikes this session:** resolver.py (project had S65 f39c3956… vs live S67 600456bf… — editing from it would have silently reverted the S67 fix), health.py (project 17ba704b… vs live b0655312…), plus config.py/compose verified clean. The hash gate caught all before damage. Operator uploaded live copies both times.

---

## PENDING AT SESSION CLOSE (do early next session, or operator does between sessions)

1. **GitHub sync — ALL seven files above.** From ~/watchdawg-repo-sync/: copy resolver.py→backend/app/services/, resolve.py+health.py→backend/app/routers/, index.html→backend/app/templates/, config.py→backend/app/, docker-compose.yml→backend/, sweep_vimeo_404.py→backend/data/ (create if needed), plus this summary → commit → push → record commit hash.
2. **Refresh project knowledge** with all seven current files (three stale-file strikes this session).
3. **Operator field report**: results of several days of batch→verify→purge cycling — how deep the dead cohort ran, any healthy/unverified verdicts from the button, whether "Resolved N" numbers recovered.
4. **Web UI verification incomplete**: green Vimeo light + Verify button flow deployed but not yet eyeballed/exercised in the browser at close (operator began testing).

---

## KEY LEARNINGS THIS SESSION

- **Providers can change the rules platform-wide overnight.** Not every failure is our bug, a block, or dead videos — check upstream (yt-dlp issues) EARLY when a provider fails uniformly. The 401-on-canary was the tell: canary failure = infrastructure, never video death.
- **Error text is a diagnostic ladder, and the rung matters:** 401 on token fetch (no anonymous auth) → "only works when logged-in" (auth demanded, none/expired supplied) → 404 AFTER auth succeeds (real per-video verdict, Session 66 rule, now with anonymous-access ambiguity eliminated) → full format line (healthy). Same video, four different meanings by rung.
- **Cookie exports fail silently by omitting HttpOnly session cookies.** A 27-line syntactically-perfect Netscape file authenticated nothing. Symptom: "only works when logged-in" WITH a cookie file supplied. Fix: re-export from a freshly-loaded logged-in tab; verify with the canary before deploying.
- **yt-dlp writes cookies BACK on exit** (session-token rotation — extends export lifetime for free). Cosmetic PermissionError on read-only test copies; the compose "read-only" comments are aspirational (no :ro flags) — intentional now.
- **Stale extractor caches produce misleading failure modes**: post-update, the cached dead OAuth token failed one rung PAST where stable failed. `--rm-cache-dir` after Vimeo-related updates.
- **Image + auto-updater + manual nightly = three-way version fight.** Baked May nightly (corrupt metadata) → startup auto-updater → stable → manual nightly. `--force-reinstall` is the repair for corrupt pip metadata. Rebuild with pinned yt-dlp queued.
- **A verdict requires healthy auth at verdict time** — hence canary-gate everything that judges videos (the sweep does; the future attempts-counter should).
- **`resolution_error` is only stamped on ATTEMPTED videos** — 14k pending rows have NULL errors simply because the batch never reached them. Momentarily misread as a lost-write bug; the missing rows turned out to be operator-skipped. Query the DB directly before theorizing.
- **Sweep-script SQLAlchemy echo noise**: engine INFO logging drowns script output when run via docker exec — cosmetic, but future scripts should set `sqlalchemy.engine` logger to WARNING.

---

# NEXT SESSIONS — QUEUE

1. **GitHub sync + project-knowledge refresh** (PENDING above) — before anything else.
2. **Attempts-counter feature** (#1, urgency reinforced AGAIN): today was the manual demo of exactly what it automates — dead videos camping until hand-triage. Must count **all transient failures (404s included, not just 403s)**; deprioritize past threshold N; badge in ⚠️ Problems; canary-gate so global outages don't pollute counts. Design: threshold value; eventual-retry policy. Operator's field data from the cycling days feeds this directly.
3. **Read the yt-dlp auto-updater scheduler job** — confirm channel/mechanism, that it can't downgrade the nightly, and whether it should target nightly until the Vimeo fix ships stable.
4. **Image rebuild with pinned/current yt-dlp in requirements.txt** — end the post-recreation pip surgery (and fix the baked corrupt May nightly).
5. **Vimeo fallback ladder** (carried, still gated on attempts-counter data): for videos that 404 intermittently but play in a browser — impersonate/referer rungs. Note: source URLs are channel-scoped (`/channels/<name>/<id>`), so the channel page is the natural referer rung.
6. **Canary durability**: consider an operator-owned Vimeo upload as the permanent canary; wire the canary check into the batch scheduler (skip batch + loud log on canary failure, zero counter pollution).
7. **Carried:** shared-ID scraper bug (three titles, one Vimeo ID 1272691); ffmpeg thumbnail permanent-failure marker; YouTube 6-hour temp cache; Reddit cookie refresh UI instructions (Vimeo's shipped today — same card pattern); channel rename ("Local"/"Online" prefixes); PIN lock removal from web UI; dead-file cleanup execution; TiviMate cold-play test (queue item #1 since Session 62); Session 63 UI eyeball; global Problems view with bulk purge.
