# WatchDawg -- Future Additions & Feature Backlog

---

## Android TV App -- Core Requirements (Phase 1-2, Session 11)

1. Full-screen video playback via ExoPlayer / Media3
2. Feed screen with video cards (thumbnail, title, artist, status badge)
3. Channel filter sidebar -- left rail, D-pad navigable, provider groupings
4. Skip a video from feed (POST /skip/{id})
5. Add to Favorites while browsing or playing (POST /favorite/{id})
6. Next button to advance queue during playback
7. Long-press OK while playing → instant Favorite (no overlay, no pause)
8. Double-tap Left/Right D-pad → ±10 second seek with visual bubble feedback
9. Resume state -- SharedPreferences saves video ID + queue + position on pause/close
10. Hold-to-confirm delete in Library (2-second progress ring on OK button)
11. DASH playback supported via ?client=tv param on /resolve/{id} -- ExoPlayer native

---

## Android TV App -- Polish (Phase 3, later session)

12. Down D-pad during playback → semi-transparent overlay showing next 3 videos in queue
13. Pre-caching next video (ConcatenatingMediaSource) -- seamless zero-black-screen transitions
14. Dynamic background tinting -- Palette API grabs dominant color from thumbnail,
    softly tints UI background. Similar to Apple TV / modern Plex aesthetic.
15. Audio-only screensaver mode -- visualizer/clock overlay when playing music,
    option to dim display to prevent burn-in while audio continues
16. Smart Shuffle (prevent repeat burnout) -- "least recently played" weighting,
    requires new DB table + backend endpoint to track play history

---

## PIN Lock System (Phase 4)

**Design:** Sources marked as "locked" are completely hidden from feed and API responses
until a correct PIN is entered. PIN lives in backend .env -- single value, set once.

**Behavior:**
- Unchecked channels → always visible, no PIN required
- Checked (locked) channels → hidden everywhere until PIN entered
- Long-press gesture on TV remote → D-pad number pad overlay appears
- Correct PIN → session token issued (in-memory only), feed reloads with all channels
- App close → token gone, locked channels hidden again on next launch
- Browser UI also respects lock -- locked channels invisible without PIN

**Backend changes needed:**
- `locked` boolean column on Channel model (DB migration)
- `WATCHDAWG_PIN` variable in .env
- `POST /auth/unlock?pin=` endpoint → returns session token
- `/feed` excludes locked channels if no valid token in request header
- Browser UI: lock checkbox per channel card in Channels tab

**Android TV changes:**
- Long-press (or Settings entry) → PIN overlay with D-pad number input
- Store session token in memory (never SharedPreferences/disk)
- Re-fetch feed on successful unlock

---

## Browser UI -- Pending Small Additions

- **🔄 Mini scrape button per rail row** (Feed tab sidebar)
  Calls POST /channel/{id}/scrape?limit=2000 inline, updates count badge on that row.
  Shows brief spinner state on the button while running. Logs result to Activity Log.
  No navigation away from Feed tab required.

- **Lock checkbox per channel** (Channels tab, part of PIN Lock Phase 4)

---

## Channel Sources -- Pending Additions

- **Fly Music YouTube channel** (Option C -- whole channel, not individual playlists)
  URL: `https://www.youtube.com/@MusicDance.FlyMusic`
  232 videos, 324K subscribers. Just paste into Add Channel -- no code change needed.
  Individual playlists can also be added separately for per-playlist control.

---

## Remote-Control Optimization

- **Long-Press Actions** -- long-press OK while playing → Favorite or Delete confirmation
  without pausing or opening a massive overlay
- **Double-Tap D-pad** -- Left/Right instantly seeks ±10 seconds with visual bubble
- **Directional Quick-Menus** -- Down during playback → next 3 videos overlay (Phase 3)
  Up during playback → audio/subtitle toggles (if applicable)

---

## Smart & Intelligent Playback (Deferred)

- **Smart Shuffle** -- "least recently played" weighting prevents repeat burnout.
  Requires new DB table (play_history) + backend endpoint. Medium effort.
- **Intelligent Volume Leveling / Night Mode** -- dynamic range compression toggle.
  Evens out audio spikes. Device-specific, low priority.
- **True Resume State** -- save entire session state (queue + shuffle order + position).
  If app crashes or TV loses power, reopening resumes exactly where left off.

---

## Ecosystem Integration (Deferred / Low Priority)

- **Android TV Watch Next Integration** -- populate native home screen launcher row
  when a video is paused mid-way. Requires Play Store listing to work properly.
- **Smarter Delete Safeguard** -- hold-to-delete progress ring already planned.
  Additional option: directional pattern confirmation (Up + OK) for extra safety.
- **Dynamic Background Tinting** -- Palette API from thumbnail dominant color.
  Tints UI background for immersive premium feel (Phase 3).

---

## Future Projects (Separate from WatchDawg TV)

- **Android Phone App** -- separate project. Requires:
  - HTTPS exposure (reverse proxy + SSL cert, e.g. Caddy or Nginx)
  - Encrypted authentication (JWT or session tokens)
  - External DNS or dynamic DNS for remote access
  - All PIN lock and auth features apply here too
  - Significantly more security hardening before exposing to internet

---

## Architecture Notes for Future Dev

- **TV app is a dumb client** -- never contacts Vimeo/YouTube/Reddit directly.
  All media resolution, scraping, and auth happens on the backend.
- **Debug console stays in browser UI only** -- TV app has no logs panel.
  localhost:6868 is the engineering control room.
- **PIN token is session-only** -- never written to disk anywhere.
  Auto-locks on app close by design. No "remember me" option.
- **DASH on TV** -- use ?client=tv param on /resolve/{id} for alternate format selector.
  Do NOT auto-purge DASH videos once TV client is live -- they play fine in ExoPlayer.
