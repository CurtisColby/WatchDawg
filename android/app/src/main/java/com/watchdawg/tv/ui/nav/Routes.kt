package com.watchdawg.tv.ui.nav

/**
 * Navigation routes — Milestone R-2.5 Home Screen Architecture.
 *
 * HOME is the new start destination. NavRail has been removed entirely.
 * All content screens are full-screen and reached from the Home Screen grid.
 *
 * Top-level sections (match NavSection enum below):
 *   Home · TV · Movies · Live TV · Music · Continue Watching ·
 *   Watch Later · Favorites · Local · Adult · Settings
 *
 * Sub-routes (unchanged):
 *   EPISODE_LIST — drill-down episode grid for a single TV series/channel.
 *   MOVIE_DETAIL — full detail screen for a single movie.
 *   PLAYER / PLAYER_DIRECT / PLAYER_DIRECT_QUEUE — playback routes.
 *
 * Queue encoding (unchanged):
 *   Large queues live in QueueHolder, not the route string, to avoid truncation.
 *   Channel names and other strings use Base64 URL-safe encoding to survive
 *   nav arg parsing (spaces, apostrophes, special chars all safe).
 *
 * Back navigation model (R-2.5):
 *   Every screen does popBackStack() on Back — no special routing logic.
 *   Long-press Back from anywhere navigates to HOME with full stack clear.
 *   Back on HOME shows exit confirmation dialog.
 */
object Routes {
    // ── Start destination ─────────────────────────────────────────────────────
    const val HOME             = "home"

    // ── Top-level section routes ───────────────────────────────────────────────
    const val TV               = "tv"
    const val MOVIES           = "movies"
    const val LIVE_TV          = "live_tv"
    const val MUSIC            = "music"
    const val CONTINUE_WATCHING = "continue_watching"
    const val WATCH_LATER      = "watch_later"
    const val FAVORITES        = "favorites"
    const val LOCAL            = "local"
    const val ADULT            = "adult"
    const val SETTINGS         = "settings"
    const val EPG               = "epg"

    // ── Sub-routes ────────────────────────────────────────────────────────────

    // Episode list drill-down.
    // channelName is Base64-encoded — spaces/apostrophes in TV show names are safe.
    const val EPISODE_LIST = "episodeList/{channelId}/{channelName}"

    fun episodeList(channelId: Int, channelName: String): String {
        val encodedName = android.util.Base64.encodeToString(
            channelName.toByteArray(Charsets.UTF_8),
            android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or android.util.Base64.NO_PADDING,
        )
        return "episodeList/$channelId/$encodedName"
    }

    // Movie detail screen.
    // The VideoDto is passed directly as a composable parameter (no MovieHolder needed).
    // Route only carries videoId as a stable back-stack key.
    const val MOVIE_DETAIL = "movieDetail/{videoId}"

    fun movieDetail(videoId: Int): String = "movieDetail/$videoId"

    // ── Player routes (unchanged) ─────────────────────────────────────────────

    // Resolve-based player. Queue lives in QueueHolder.idQueue.
    const val PLAYER = "player/{videoId}/{startIndex}"

    fun player(videoId: Int, startIndex: Int = 0): String = "player/$videoId/$startIndex"

    // Direct-URL player (single item — URL in route).
    const val PLAYER_DIRECT = "playerDirect/{url}?title={title}"

    fun playerDirect(streamUrl: String, title: String = "Now Playing"): String {
        val encodedUrl = android.util.Base64.encodeToString(
            streamUrl.toByteArray(Charsets.UTF_8),
            android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or android.util.Base64.NO_PADDING,
        )
        val encodedTitle = android.util.Base64.encodeToString(
            title.toByteArray(Charsets.UTF_8),
            android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or android.util.Base64.NO_PADDING,
        )
        return "playerDirect/$encodedUrl?title=$encodedTitle"
    }

    // Direct-URL queue player. Queue lives in QueueHolder.urlQueue.
    const val PLAYER_DIRECT_QUEUE = "playerDirectQueue/{startIndex}"

    fun playerDirectQueue(startIndex: Int = 0): String = "playerDirectQueue/$startIndex"

    fun decode(encoded: String): String = try {
        String(
            android.util.Base64.decode(
                encoded,
                android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or android.util.Base64.NO_PADDING,
            ),
            Charsets.UTF_8,
        )
    } catch (e: Exception) { "" }
}

/**
 * Home Screen sections — displayed as cards in the full-screen Home grid.
 *
 * Milestone R-2.5: NavRail removed. NavSection is now used exclusively by
 * HomeScreen to populate the card grid and navigate to section routes.
 *
 * PIN visibility rules (enforced by HomeScreen / HomeViewModel):
 *  - TV, MOVIES, LIVE_TV, MUSIC: always visible
 *  - CONTINUE_WATCHING: always visible (adult content excluded server-side)
 *  - WATCH_LATER: always visible
 *  - FAVORITES: always visible (content filtered by lock state server-side)
 *  - LOCAL: always visible (downloaded files only, no locked content)
 *  - ADULT: only rendered when PIN is unlocked — structurally absent when locked
 *  - SETTINGS: always visible
 */
enum class NavSection(val route: String, val label: String) {
    TV(Routes.TV, "TV"),
    MOVIES(Routes.MOVIES, "Movies"),
    LIVE_TV(Routes.LIVE_TV, "Live TV"),
    MUSIC(Routes.MUSIC, "Music"),
    CONTINUE_WATCHING(Routes.CONTINUE_WATCHING, "Continue Watching"),
    WATCH_LATER(Routes.WATCH_LATER, "Watch Later"),
    FAVORITES(Routes.FAVORITES, "Favorites"),
    LOCAL(Routes.LOCAL, "Local"),
    ADULT(Routes.ADULT, "Adult"),
    EPG(Routes.EPG, "EPG"),
    SETTINGS(Routes.SETTINGS, "Settings"),
}
