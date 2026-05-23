# WatchDawg -- Current State Summary
**Session 10 End -- May 23, 2026**

---

## System Overview

WatchDawg is a Dockerized FastAPI backend running on `colby@PlexServer` at `localhost:6868`. It discovers music videos from multiple sources, resolves direct stream URLs via yt-dlp, and serves a browser-based control deck UI at the root URL.

**Machine:** Linux Mint, username `colby`, machine `PlexServer`
**Container:** `watchdawg-backend`
**Project dir:** `~/watchdawg-backend`
**Database:** SQLite at `~/watchdawg-backend/data/watchdawg.db` (volume mounted)
**NAS downloads:** `/media/colby/NAS1/WatchDawg` (mapped to `/music_videos` in container)

---

## CRITICAL: Deploy Method (Volume-Mounted)

Source code is volume-mounted. The container reads live from `~/watchdawg-backend/app/`.

**Deploy workflow (no docker cp needed):**
```bash
cp ~/Downloads/<file>.py ~/watchdawg-backend/app/routers/<file>.py
cp ~/Downloads/<file>.py ~/watchdawg-backend/app/services/<file>.py
cp ~/Downloads/<file>.py ~/watchdawg-backend/app/tasks/<file>.py
cp ~/Downloads/index.html ~/watchdawg-backend/app/templates/index.html
docker restart watchdawg-backend
```

**Image rebuild** (only needed when requirements.txt or Dockerfile changes):
```bash
cd ~/watchdawg-backend && docker compose build --no-cache && docker compose up -d
```

All source files live at `~/watchdawg-backend/app/` and are the source of truth.

---

## CRITICAL: feed_work.py vs feed.py

`feed_work.py` is the SOURCE FILE for the feed router with full `channel_ids` sidebar
filtering support. Its content was deployed as `app/routers/feed.py` in Session 10.
`main.py` imports `from app.routers.feed import router` -- this is correct.
Never import `feed_work` directly -- it does not exist inside the container's routers/.

---

## What's Working (Cumulative -- Sessions 1-10)

### Core Pipeline
- **Reddit scraping** via public JSON API -- hot listings, score-ranked, deduped
- **Vimeo channel discovery** via yt-dlp flat extraction -- full pagination, all videos
- **YouTube channel/playlist support** via yt-dlp playlist/channel type
- **Channel management UI** -- add/remove/enable/disable/scrape/clear per channel
- **Feed filtering** -- disabled channels hidden from feed instantly
- **Background scheduler** -- scrapes all enabled channels + resolves pending/expired every 30 min

### Channels & Friendly Names
- **Friendly name on add** -- Add Channel form has optional "Friendly Name" field
- **Rename existing channels** -- ✏️ pencil button opens inline rename form
- **PATCH /channel/{id}/rename** -- updates name in DB, reflected everywhere immediately
- **Subfolder downloads** -- favorites download to `/music_videos/{channel.name}/`

### Scraper (UPDATED Session 10)
- **Per-channel scrape limit raised** -- `le=5000`, default 500 (was le=500)
- **Global scrape limit raised** -- `le=5000`, default 2000 (was le=500, default 200)
- **UI scrape calls** -- both "Scrape All" and per-channel 🔄 buttons send `limit=2000`
- **Cross-channel Vimeo dedup** -- same numeric Vimeo ID blocked globally at ingest
  (confirmed working: 9,615 sum across channels vs 9,513 unique = only 102 true dupes)
- **Savepoint per insert** -- `begin_nested()` per row, batch survives individual failures
- **Skip list remains global** -- skipped video stays skipped across all channels

### Resolution Engine
- **Hard yt-dlp timeout** -- ProcessPoolExecutor with asyncio.wait_for(timeout=90s)
- **ProcessPoolExecutor** -- max_workers=4, reusable pool
- **Muxed-first FORMAT_SELECTOR** -- prioritizes muxed streams (both vcodec+acodec)
- **Auto-delete on permanent failure** -- 404/private/removed auto-deleted
- **Adaptive TTL** -- MP4: 3hr, HLS/DASH: 20min
- **Auto-dedup on resolution** -- CDN fingerprint checked after every fresh resolve
- **Dedup redirect** -- duplicate deleted → playback redirects to keeper's stream URL
- **Domain-gated CDN fingerprinting** -- Vimeo-only, eliminates YouTube false positives
- **Scheduled dedup sweep** -- every 6 hours
- **Auto-purge DASH on scheduler** -- every 30-min resolve tick

### Feed Router (UPDATED Session 10 -- feed_work.py content deployed as feed.py)
- **GET /feed?channel_ids=** -- server-side channel filter, sidebar-driven
- **GET /feed/ids?channel_ids=** -- ID list for Shuffle All, respects sidebar
- **POST /feed/scrape?limit=2000** -- global scrape, le=5000
- **Filter logic** -- selected_ids intersected with enabled_channel_ids, empty = no results

### UI Layout (UPDATED Session 10)
- **Independent sidebar scroll** -- all three tabs (Feed, Favorites, Library) have
  sidebars that scroll independently from the main content area. Fixed by making
  .tab-content.active a flex column, .feed-layout uses flex:1 + overflow:hidden,
  sidebar and feed-main each get height:100% + overflow-y:auto
- **No gap at top** -- removed position:sticky from .controls (breaks inside overflow:hidden)
- **Two scroll buttons** -- independent ⬆ and ⬇ fixed buttons, each show/hide based
  on scroll position. Both visible when in the middle of a long feed.
- **Channels tab scrollable** -- .channel-manager gets overflow-y:auto + flex:1

### Feed Tab UI
- **Channel filter sidebar** -- server-side filtering via ?channel_ids= param (NOW WORKING)
  Previously feed_work.py was never registered -- old feed.py ignored channel_ids entirely
- **Resolve All respects sidebar** -- only resolves selected channels
- **Reset Failed respects sidebar** -- same
- **Stop Resolve button** -- appears during batch, calls /resolve/stop
- **Infinite scroll** -- IntersectionObserver scoped to .feed-main (not window)
- **Shuffle All** -- fetches full ID list, respects sidebar filter
- **Global video counter** -- orange badge in header

### Library Tab UI (FIXED Session 10)
- **Subfolder filter now works correctly** -- was broken due to escapeAttr() key mismatch:
  onclick passed escaped key (e.g. `art_32_nudes`) but libSidebarState used raw key
  (`art nudes`). Fixed by storing raw key in data-lib-key attribute on each row,
  toggleLibFolderByEl() reads dataset.libKey -- no escaping in state lookup.
- **libSidebarState reset on load** -- stale keys from previous scans no longer
  corrupt the allChecked fast-path. prevState preserved for folders that still exist.
- **_getLibVisibleItems uses === true** -- undefined keys don't pass through

### Favorites Tab UI
- **Channel filter sidebar** -- client-side filtering, no server re-fetch
- **Channel name badge** -- purple folder badge on each card
- **Fav button on pending/failed cards** -- can favorite before resolved

### Library Tab
- **Recursive subfolder scanning** -- os.walk() traverses all subdirs
- **Thumbnail generation** -- ffmpeg frame grab at 5s, .watchdawg_thumb.jpg sidecar
- **Generate Thumbs auto-loop** -- loops until server returns 0 generated
- **Delete flow** -- validates path, adds to skip list, removes DB records, deletes file

### Favorites Tab
- **Download to channel subfolder** -- /music_videos/{channel.name}/
- **Uncategorized fallback** -- no channel → /music_videos/Uncategorized/
- **Duplicate download guard** -- checks disk before re-downloading
- **Retry download** -- button on failed/pending favorites

### Skip / Blocklist
- **Encrypted at rest** -- Fernet + HMAC hash for fast lookups
- **Skip button** on feed cards -- adds to blocklist, removes from feed
- **Library delete** -- also adds to blocklist automatically
- **Clear Blocklist** -- clears entire blocklist

### Debug Console
- **Floating 🐛 panel** -- live server logs polled from /debug/logs every 3s
- **Tabs:** All / Proxy / Stream / Errors
- **Download Log button** -- saves full log as timestamped .txt
- **Lives in browser UI only** -- Android TV app has NO debug console (by design)

---

## DB State (End of Session 10)

- **9,513 unique videos** in feed across all channels
- **9,615 sum** across channels (102 cross-channel Vimeo duplicates blocked by dedup)
- **9,038 videos** total in DB (header count at session end)
- **24 files** in local library (/music_videos)
- Cross-channel Vimeo dedup confirmed working correctly
- All channel scrape limits now at le=5000 -- ready for deep channel scrapes

---

## API Endpoints Reference (Complete)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | System health + DB connectivity |
| GET | `/feed?limit=&offset=&channel_ids=` | Video feed (sidebar filter, enabled channels) |
| GET | `/feed/ids?channel_ids=` | Video IDs only -- Shuffle All |
| POST | `/feed/scrape?limit=2000` | Scrape all enabled channels (le=5000) |
| GET | `/channel` | List all channels with video counts |
| POST | `/channel` | Add channel (auto-detects type, optional friendly name) |
| DELETE | `/channel/{id}` | Delete a channel |
| PATCH | `/channel/{id}` | Enable/disable a channel |
| PATCH | `/channel/{id}/rename` | Update channel friendly display name |
| POST | `/channel/{id}/scrape?limit=2000` | Scrape single channel (le=5000) |
| DELETE | `/channel/{id}/videos` | Clear all videos from a channel |
| GET | `/resolve/{id}?force=` | Resolve video to direct stream URL |
| POST | `/resolve/batch?limit=500&channel_ids=` | Batch resolve (channel-scoped) |
| POST | `/resolve/stop` | Signal running batch to stop after current video |
| POST | `/resolve/reset-failed?channel_ids=` | Reset failed to pending (channel-scoped) |
| POST | `/resolve/backfill-thumbnails?limit=50` | yt-dlp metadata pass for missing thumbnails |
| POST | `/resolve/purge-dash` | Delete all DASH-only videos |
| POST | `/resolve/purge-dead` | Delete all failed videos |
| POST | `/resolve/purge-duplicates` | Delete duplicate CDN files, keep best copy |
| POST | `/skip` | Add video to skip list (encrypted) |
| GET | `/skip/count` | Skip list entry count |
| POST | `/skip/clear` | Clear entire skip/blocklist |
| POST | `/favorite/{id}` | Favorite + trigger NAS download |
| GET | `/favorite` | List favorites + channel_id/name/source_provider/stream_url |
| DELETE | `/favorite/{id}?delete_file=` | Remove favorite, optionally delete file |
| POST | `/favorite/{id}/retry` | Retry failed/pending download |
| GET | `/library` | Recursive scan of /music_videos |
| GET | `/library/stream/{path:path}` | Stream local file (range-capable) |
| GET | `/library/thumb/{path:path}` | Serve ffmpeg-generated sidecar thumbnail |
| POST | `/library/generate-thumbnails?limit=20` | ffmpeg frame-grab for unmatched files |
| DELETE | `/library/file?relative_path=` | Delete file + add to blocklist |
| GET | `/proxy/stream?url=` | Proxy CDN stream (HLS, YouTube CDN) |
| GET | `/debug/logs?n=500` | Last N in-memory log entries |

---

## FORMAT_SELECTOR Logic (resolver.py) -- Muxed-First

Priority order (all require both vcodec AND acodec present = muxed stream):
1. Best muxed MP4 <=1080p, direct HTTP
2. Best muxed MP4 any height, direct HTTP
3. Best muxed any format, direct HTTP
4. Best muxed, any protocol except DASH
5. Best muxed, no protocol restriction
6. Best non-DASH (may be video-only, last resort)
7. Best anything (absolute last resort)

**DASH videos:** Fail in browser -- marked as transient failure.
**Android TV fix:** Will use `?client=tv` param on /resolve/{id} to allow DASH.
ExoPlayer handles DASH natively. Do NOT auto-purge DASH once TV client is live.

---

## Scheduler Jobs (scheduler.py)

| Job | Interval | What it does |
|---|---|---|
| scrape_job | 30 min | Scrapes all enabled channels (50 videos/channel) |
| resolve_job | 30 min | Resolves 200 pending + 100 expired; auto-purges DASH |
| dedup_job | 6 hours | CDN fingerprint sweep, deletes lower-scored duplicates |

Note: scheduler scrape_job still uses limit=50/channel. Only manual scrapes use 2000.

---

## File Structure (Inside Container AND ~/watchdawg-backend/app/)

```
app/
├── main.py
├── config.py
├── database.py
├── models.py
├── encryption.py
├── hashing.py
├── __init__.py
├── routers/
│   ├── channel.py       -- le=5000 scrape limit (Session 10)
│   ├── favorite.py
│   ├── feed.py          -- feed_work.py content: channel_ids filter, /feed/ids (Session 10)
│   ├── health.py
│   ├── library.py
│   ├── proxy.py
│   ├── resolve.py
│   ├── skip.py
│   └── web_ui.py
├── providers/
│   ├── base.py
│   ├── reddit.py
│   ├── playlist.py
│   └── vimeo_rss.py
├── services/
│   ├── resolver.py
│   └── scraper.py
├── tasks/
│   └── scheduler.py
└── templates/
    └── index.html       -- Session 10 UI fixes
```

---

## Next Session -- Android TV APK (Session 11)

### Build Order (Agreed Plan)

**Phase 1 -- Foundation**
1. Android Studio project: Kotlin, Media3/ExoPlayer, Compose for TV (androidx.tv)
2. Network layer: Retrofit + OkHttp pointing at `http://{PlexServer_IP}:6868`
3. Feed screen: TvLazyVerticalGrid, video cards with thumbnail/title/status
4. Channel filter sidebar: left-drawer, D-pad accessible, provider groupings
5. Full-screen playback: GET /resolve/{id} → ExoPlayer, DASH supported via ?client=tv

**Phase 2 -- Core interactions**
6. Skip (POST /skip/{id}), Favorite (POST /favorite/{id}), queue/next/auto-advance
7. Long-press OK → Favorite while playing (no overlay needed)
8. Double-tap left/right D-pad → ±10s seek
9. Resume state: SharedPreferences saves current video ID + queue + position on pause
10. Hold-to-confirm delete in Library (2-second progress ring)

**Phase 3 -- Polish**
11. Down-during-playback → semi-transparent next-3-videos overlay
12. Pre-caching next video (ConcatenatingMediaSource, mostly free)
13. Dynamic background tinting (Palette API from thumbnail)

**Phase 4 -- PIN Lock System**
14. Backend: `locked` boolean on Channel model + PIN in .env
15. Backend: POST /auth/unlock?pin= → session token (in-memory, resets on restart)
16. Backend: /feed excludes locked channels unless valid token provided
17. Browser UI: lock checkbox per channel card
18. Android TV: long-press gesture → D-pad PIN entry overlay
19. On correct PIN: re-fetch feed, all channels visible until app closes
20. Auto-lock on app close -- token never persisted to disk

### Key Technical Decisions
- **DASH on TV:** `?client=tv` param on /resolve/{id} uses alternate FORMAT_SELECTOR
  that allows DASH. ExoPlayer handles it natively.
- **No debug console on TV** -- browser UI at localhost:6868 is the control room
- **Dumb client rule** -- TV app never contacts Vimeo/YouTube/Reddit directly
- **PIN in .env** -- single PIN, set once, backend validates. No DB storage needed.
- **minSdk:** 21 (Android 5.0) -- covers all Android TV hardware
- **targetSdk:** 35
- **Kotlin:** 2.0+
- **Media3:** Latest stable (replaces legacy ExoPlayer)
- **UI:** Jetpack Compose for TV (androidx.tv:tv-compose)
- **APK delivery:** Debug APK, sideload via `adb install watchdawg-tv.apk`

### Features Deferred
- Smart shuffle with play history weighting (needs new DB table + backend endpoint)
- Android TV Watch Next home screen row (requires Play Store listing)
- Intelligent volume leveling / Night Mode (device-specific, low priority)
- Audio-only screensaver (medium effort, nice-to-have later)
- Phone app with external exposure (separate project, needs auth + HTTPS)
- Whole-channel YouTube source: `https://www.youtube.com/@MusicDance.FlyMusic`
  (just paste URL into Add Channel -- no code change needed, reminder to do it)

### Build Environment
- Android Studio (latest stable)
- AVD: Android TV emulator OR physical Fire TV Stick / NVIDIA Shield
- ADB over WiFi or USB for sideloading

### Pending UI Additions (index.html only, small)
- 🔄 Mini scrape button per channel row in Feed rail
  (calls POST /channel/{id}/scrape?limit=2000, updates count badge inline)

---

## Key Lessons Learned (Sessions 1-10)

1. **ProcessPoolExecutor vs ThreadPoolExecutor for yt-dlp** -- threads can't be killed,
   processes can. asyncio.wait_for() + ProcessPoolExecutor = true hard timeout.
2. **Dedup false positives were real** -- Pattern 2 (/video/{hash}/) was broad enough
   to match YouTube CDN paths. Domain guard eliminates this.
3. **Dedup should redirect, not 404** -- duplicate deleted → keeper's URL returned.
4. **channel_ids filter must be server-side** -- client-side only is useless, data still
   comes from server. feed_work.py was never registered -- root cause of filter bleed.
5. **escapeAttr in onclick = state key mismatch** -- HTML attribute escaping must never
   be used for JS function arguments that key into state objects. Use data attributes.
6. **position:sticky inside overflow:hidden is ignored** -- causes phantom gap.
   Remove sticky, use flex layout instead for sidebar scroll independence.
7. **libSidebarState must reset on each load** -- stale true-keys from previous renders
   corrupt allChecked fast-path, showing all items when only some are selected.
8. **feed_work.py deployment confusion** -- the file existed in project root but was
   never copied to app/routers/. Summary note said "active version" but it wasn't.
   Always verify with grep on the live file before assuming a fix is deployed.
9. **Cross-channel Vimeo dedup is working correctly** -- Pure Chokolate shows 1,227
   not 3,275 because the remaining ~2,000 are already in DB under other channels.
   9,615 channel sum vs 9,513 unique confirms healthy dedup with minimal waste.
10. **PIN lock architecture** -- must be backend-enforced, not client-side only.
    Token is session-only (in-memory), never persisted. Auto-locks on app close.
