package com.watchdawg.tv.ui.nav

/**
 * Navigation routes.
 *
 * Large queues (Play All / Shuffle All) are stored in QueueHolder rather than
 * encoded in the route string. Android's nav system truncates long route
 * strings which corrupts queues with hundreds or thousands of IDs.
 *
 * Route            | Queue source
 * -----------------|-------------------------------------------------
 * player/{id}      | QueueHolder.idQueue (video IDs, resolved on demand)
 * playerDirect/{u} | QueueHolder.urlQueue (direct stream URLs)
 *                  | Single-item play: urlQueue is empty, url in route
 *
 * Milestone D additions:
 *  - CONTINUE_WATCHING — history endpoint, always visible, no PIN required
 *  - WATCH_LATER — watchlist endpoint, always visible, no PIN required
 *
 * Milestone F additions:
 *  - EPISODE_LIST — drill-down episode grid for a single TV series/channel.
 *    Carries channelId (Int) and channelName (String, Base64-encoded to safely
 *    survive nav arg parsing — channel names can contain spaces and apostrophes).
 *
 * Nav rail order (Milestone D):
 *   Feed · Continue Watching · Watch Later · Favorites · Library · Channels · Settings
 */
object Routes {
    const val FEED              = "feed"
    const val CONTINUE_WATCHING = "continue_watching"
    const val WATCH_LATER       = "watch_later"
    const val CHANNELS          = "channels"
    const val FAVORITES         = "favorites"
    const val LIBRARY           = "library"
    const val SETTINGS          = "settings"

    // Resolve-based player. Queue lives in QueueHolder.idQueue.
    // The route only carries videoId (to resolve first) and startIndex.
    const val PLAYER = "player/{videoId}/{startIndex}"

    fun player(videoId: Int, startIndex: Int = 0): String =
        "player/$videoId/$startIndex"

    // Direct-URL player. For single items the URL is in the route.
    // For queues (Library/Favorites Play All) the URL list is in QueueHolder.urlQueue
    // and the route carries only the start index and a flag.
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

    // Milestone F: Episode list drill-down.
    // channelName is Base64-encoded so spaces/apostrophes/special chars in TV
    // show names don't corrupt the nav route string.
    const val EPISODE_LIST = "episodeList/{channelId}/{channelName}"

    fun episodeList(channelId: Int, channelName: String): String {
        val encodedName = android.util.Base64.encodeToString(
            channelName.toByteArray(Charsets.UTF_8),
            android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or android.util.Base64.NO_PADDING,
        )
        return "episodeList/$channelId/$encodedName"
    }

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
 * Nav rail sections in display order.
 *
 * PIN visibility rules (Milestone D):
 *  - FEED, CONTINUE_WATCHING, WATCH_LATER, CHANNELS, SETTINGS: always visible
 *  - FAVORITES: always visible — content filtered by lock state (public only when locked)
 *  - LIBRARY: hidden when locked, visible after PIN
 */
enum class NavSection(val route: String, val label: String) {
    FEED(Routes.FEED, "Feed"),
    CONTINUE_WATCHING(Routes.CONTINUE_WATCHING, "Continue Watching"),
    WATCH_LATER(Routes.WATCH_LATER, "Watch Later"),
    FAVORITES(Routes.FAVORITES, "Favorites"),
    LIBRARY(Routes.LIBRARY, "Library"),
    CHANNELS(Routes.CHANNELS, "Channels"),
    SETTINGS(Routes.SETTINGS, "Settings"),
}
