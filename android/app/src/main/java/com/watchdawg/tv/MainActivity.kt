package com.watchdawg.tv

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Surface
import androidx.tv.material3.SurfaceDefaults
import androidx.tv.material3.Text
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.prefs.QueueHolder
import com.watchdawg.tv.ui.WatchDawgViewModelFactory
import com.watchdawg.tv.ui.adult.AdultScreen
import com.watchdawg.tv.ui.adult.AdultViewModel
import com.watchdawg.tv.ui.auth.PinPadOverlay
import com.watchdawg.tv.ui.auth.PinViewModel
import com.watchdawg.tv.ui.auth.globalUnlockGesture
import com.watchdawg.tv.ui.continuewatching.ContinueWatchingScreen
import com.watchdawg.tv.ui.continuewatching.ContinueWatchingViewModel
import com.watchdawg.tv.ui.favorites.FavoritesScreen
import com.watchdawg.tv.ui.home.HomeScreen
import com.watchdawg.tv.ui.home.HomeViewModel
import com.watchdawg.tv.ui.library.FavoritesViewModel
import com.watchdawg.tv.ui.library.LibraryScreen
import com.watchdawg.tv.ui.library.LibraryViewModel
import com.watchdawg.tv.ui.epg.EpgScreen
import com.watchdawg.tv.ui.epg.EpgViewModel
import com.watchdawg.tv.ui.livetv.LiveTvScreen
import com.watchdawg.tv.ui.livetv.LiveTvViewModel
import com.watchdawg.tv.ui.movies.MovieDetailScreen
import com.watchdawg.tv.ui.movies.MovieDetailViewModel
import com.watchdawg.tv.ui.movies.MoviesScreen
import com.watchdawg.tv.ui.movies.MoviesViewModel
import com.watchdawg.tv.ui.music.MusicScreen
import com.watchdawg.tv.ui.music.MusicViewModel
import com.watchdawg.tv.ui.nav.Routes
import com.watchdawg.tv.ui.player.PlayerScreen
import com.watchdawg.tv.ui.player.PlayerStartMode
import com.watchdawg.tv.ui.player.PlayerViewModel
import com.watchdawg.tv.ui.series.EpisodeListScreen
import com.watchdawg.tv.ui.series.SeriesViewModel
import com.watchdawg.tv.ui.settings.SettingsScreen
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.WatchDawgTheme
import com.watchdawg.tv.ui.tv.TVScreen
import com.watchdawg.tv.ui.tv.TVViewModel
import com.watchdawg.tv.ui.watchlater.WatchLaterScreen
import com.watchdawg.tv.ui.watchlater.WatchLaterViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // ── Keep the screen alive for the entire app session ──────────────────
        // FLAG_KEEP_SCREEN_ON is set once here and intentionally NEVER cleared
        // in onPause(). Clearing it on pause was the root cause of the 30–60
        // minute crash: Projectivy Launcher fires onPause/onResume on our
        // activity periodically while it manages its own overlay UI. Each cycle
        // stripped the flag, eventually allowing the TV's Ambient Mode to
        // reclaim the activity. The Android docs confirm the system already
        // allows the screen to turn off normally when the app is backgrounded —
        // we do not need to clear it manually. It is only fully released in
        // onDestroy() when the user explicitly exits the app.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        setContent {
            WatchDawgTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    colors = SurfaceDefaults.colors(containerColor = WatchDawgColors.Background),
                ) { WatchDawgRoot(onFinish = { finish() }) }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Re-add the flag on every resume as a belt-and-suspenders guard.
        // Some OEM TV firmware strips window flags on resume — this ensures
        // the flag is always present when WatchDawg is the foreground activity.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        Graph.playerManagerIfExists()?.reattachSurface()
    }

    // onPause() intentionally has NO clearFlags() call.
    // See onCreate() comment above for the full explanation.

    override fun onStop() {
        super.onStop()
        Graph.playerManagerIfExists()?.detachSurface()
    }

    override fun onDestroy() {
        super.onDestroy()
        // Only clear the flag when the user is truly done with the app.
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        TokenHolder.clear()
        QueueHolder.clear()
        Graph.releasePlayerManager()
    }
}

@Composable
private fun WatchDawgRoot(onFinish: () -> Unit) {
    val context = LocalContext.current
    val navController = rememberNavController()
    val factory = remember { WatchDawgViewModelFactory() }

    // ── ViewModels hoisted at root — survive navigation ───────────────────────
    val homeViewModel:    HomeViewModel    = viewModel(factory = factory)
    val tvViewModel:      TVViewModel      = viewModel(factory = factory)
    val seriesViewModel:  SeriesViewModel  = viewModel(factory = factory)
    val pinViewModel:     PinViewModel     = viewModel(factory = factory)
    val moviesViewModel:  MoviesViewModel  = viewModel(factory = factory)
    val musicViewModel:   MusicViewModel   = viewModel(factory = factory)
    val adultViewModel:   AdultViewModel   = viewModel(factory = factory)
    // Milestone I — Live TV: hoisted so channel list survives Back → re-enter
    val liveTvViewModel:  LiveTvViewModel  = viewModel(factory = factory)
    // Session 39 — EPG: hoisted so channel index survives player → back → surf
    val epgViewModel:     EpgViewModel     = viewModel(factory = factory)

    var showPinPad     by remember { mutableStateOf(false) }
    var showExitDialog by remember { mutableStateOf(false) }

    // Tracks which series card was last tapped so SeriesScreen can restore
    // focus to it when Back from EpisodeList returns to the series grid.
    var lastTappedSeriesChannelId by remember { mutableIntStateOf(-1) }

    val backStack    by navController.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route ?: Routes.HOME
    val onHome       = currentRoute == Routes.HOME
    val onPlayer     = currentRoute.startsWith("player")

    // ── Back navigation ───────────────────────────────────────────────────────

    // On Home: show exit dialog.
    BackHandler(enabled = onHome && !showPinPad && !showExitDialog) {
        showExitDialog = true
    }

    // ── Long-press Back: instant jump to Home from anywhere ───────────────────
    // Implemented via onKeyEvent on the root Box. We track the KeyDown timestamp
    // and on KeyUp check elapsed time — ≥ 600ms = long press → navigate to Home.
    // Short Back presses fall through to the composable BackHandler chain above,
    // which handles per-screen Back normally (popBackStack).
    var backDownMs by remember { mutableLongStateOf(0L) }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WatchDawgColors.Background)
            .globalUnlockGesture(enabled = !showPinPad) { showPinPad = true }
            .onKeyEvent { event ->
                when {
                    event.key == Key.Back && event.type == KeyEventType.KeyDown -> {
                        backDownMs = System.currentTimeMillis()
                        false // let KeyDown propagate so BackHandler can see it
                    }
                    event.key == Key.Back && event.type == KeyEventType.KeyUp -> {
                        val held = System.currentTimeMillis() - backDownMs
                        if (held >= 600L && !onHome && !showPinPad && !showExitDialog) {
                            // Long-press: clear stack back to Home
                            navController.navigate(Routes.HOME) {
                                popUpTo(Routes.HOME) { inclusive = true }
                                launchSingleTop = true
                            }
                            true // consumed — suppress normal Back
                        } else {
                            false // short press — let BackHandler chain handle it
                        }
                    }
                    else -> false
                }
            },
    ) {
        // ── Nav host — full screen, no rail ───────────────────────────────────
        NavHost(
            navController    = navController,
            startDestination = Routes.HOME,
            modifier         = Modifier.fillMaxSize(),
        ) {

            // ── Home ──────────────────────────────────────────────────────────
            composable(Routes.HOME) {
                HomeScreen(
                    viewModel  = homeViewModel,
                    onNavigate = { route ->
                        navController.navigate(route) {
                            // Keep Home as the single back-stack root so Back
                            // from any section always returns here cleanly.
                            launchSingleTop = true
                        }
                    },
                )
            }

            // ── TV ────────────────────────────────────────────────────────────
            composable(Routes.TV) {
                TVScreen(
                    tvViewModel               = tvViewModel,
                    seriesViewModel           = seriesViewModel,
                    lastTappedSeriesChannelId = lastTappedSeriesChannelId,
                    onSeriesTap = { channelId, channelName ->
                        lastTappedSeriesChannelId = channelId
                        navController.navigate(Routes.episodeList(channelId, channelName))
                    },
                    onPlay = { videoId, queue, index, hlsMode ->
                        QueueHolder.setIdQueue(queue, index, hls = hlsMode)
                        navController.navigate(Routes.player(videoId, index))
                    },
                )
            }

            // ── Episode list drill-down ───────────────────────────────────────
            composable(
                route = Routes.EPISODE_LIST,
                arguments = listOf(
                    navArgument("channelId")   { type = NavType.IntType },
                    navArgument("channelName") { type = NavType.StringType },
                ),
            ) { entry ->
                val channelId   = entry.arguments?.getInt("channelId") ?: return@composable
                val encodedName = entry.arguments?.getString("channelName") ?: ""
                val channelName = Routes.decode(encodedName)
                EpisodeListScreen(
                    channelId   = channelId,
                    channelName = channelName,
                    viewModel   = seriesViewModel,
                    onPlay = { videoId, queue, index, hlsMode ->
                        QueueHolder.setIdQueue(queue, index, hls = hlsMode)
                        navController.navigate(Routes.player(videoId, index))
                    },
                    onBack = { navController.popBackStack() },
                )
            }

            // ── Movies ────────────────────────────────────────────────────────
            composable(Routes.MOVIES) {
                MoviesScreen(
                    viewModel = moviesViewModel,
                    onPlay = { videoId, queue, index, hlsMode ->
                        QueueHolder.setIdQueue(queue, index, hls = hlsMode)
                        navController.navigate(Routes.player(videoId, index))
                    },
                    onBack = { navController.popBackStack() },
                )
            }

            // ── Movie detail ──────────────────────────────────────────────────
            composable(
                route = Routes.MOVIE_DETAIL,
                arguments = listOf(
                    navArgument("videoId") { type = NavType.IntType },
                ),
            ) {
                val video = QueueHolder.pendingVideo
                if (video == null) {
                    LaunchedEffect(Unit) { navController.popBackStack() }
                    return@composable
                }
                val vm: MovieDetailViewModel = viewModel(factory = factory)
                MovieDetailScreen(
                    video     = video,
                    viewModel = vm,
                    onPlay = { videoId, positionMs ->
                        QueueHolder.setIdQueue(listOf(videoId), 0)
                        QueueHolder.resumePositionMs = positionMs
                        navController.navigate(Routes.player(videoId, 0))
                    },
                    onBack = {
                        QueueHolder.pendingVideo = null
                        navController.popBackStack()
                    },
                )
            }

            // ── Live TV — Milestone I ─────────────────────────────────────────
            // Tune-in routes directly to PLAYER_DIRECT — live HLS/MPEG-TS streams
            // need no resolve step; ExoPlayer handles both natively.
            composable(Routes.LIVE_TV) { liveTvEntry ->
                LiveTvScreen(
                    viewModel    = liveTvViewModel,
                    currentEntry = liveTvEntry,
                    onTuneIn  = { streamUrl, channelName ->
                        navController.navigate(Routes.playerDirect(streamUrl, channelName))
                    },
                    onBack = { navController.popBackStack() },
                )
            }

            // ── Music — Milestone R-4 ─────────────────────────────────────────
            composable(Routes.MUSIC) {
                MusicScreen(
                    viewModel = musicViewModel,
                    onPlay = { videoId, queue, index, hlsMode ->
                        QueueHolder.setIdQueue(queue, index, hls = hlsMode)
                        navController.navigate(Routes.player(videoId, index))
                    },
                    onBack = { navController.popBackStack() },
                )
            }

            // ── Continue Watching ─────────────────────────────────────────────
            composable(Routes.CONTINUE_WATCHING) {
                val vm: ContinueWatchingViewModel = viewModel(factory = factory)
                ContinueWatchingScreen(
                    viewModel = vm,
                    onResumePlay = { videoId, queue, index, positionMs ->
                        QueueHolder.setIdQueue(queue, index, hls = true)
                        QueueHolder.resumePositionMs = positionMs
                        navController.navigate(Routes.player(videoId, index))
                    },
                )
            }

            // ── Watch Later ───────────────────────────────────────────────────
            composable(Routes.WATCH_LATER) {
                val vm: WatchLaterViewModel = viewModel(factory = factory)
                WatchLaterScreen(
                    viewModel = vm,
                    onPlay = { videoId, queue, index, hlsMode ->
                        QueueHolder.setIdQueue(queue, index, hls = hlsMode)
                        navController.navigate(Routes.player(videoId, index))
                    },
                )
            }

            // ── Favorites ─────────────────────────────────────────────────────
            composable(Routes.FAVORITES) {
                val vm: FavoritesViewModel = viewModel(factory = factory)
                FavoritesScreen(
                    viewModel = vm,
                    onPlayById = { videoId, queue, index ->
                        QueueHolder.setIdQueue(queue, index)
                        navController.navigate(Routes.player(videoId, index))
                    },
                    onPlayStreamUrl = { relativeUrl, title ->
                        QueueHolder.setUrlQueue(listOf(relativeUrl), 0)
                        navController.navigate(Routes.playerDirect(relativeUrl, title))
                    },
                    onPlayQueue = { ids, index ->
                        QueueHolder.setIdQueue(ids, index)
                        if (ids.isNotEmpty()) {
                            navController.navigate(Routes.player(ids[index], index))
                        }
                    },
                )
            }

            // ── Local (was Library) ───────────────────────────────────────────
            composable(Routes.LOCAL) {
                val vm: LibraryViewModel = viewModel(factory = factory)
                LibraryScreen(
                    viewModel = vm,
                    onPlayStreamUrl = { relativeUrl, title ->
                        QueueHolder.setUrlQueue(listOf(relativeUrl), 0)
                        navController.navigate(Routes.playerDirect(relativeUrl, title))
                    },
                    onPlayQueue = { urls, index ->
                        QueueHolder.setUrlQueue(urls, index)
                        navController.navigate(Routes.playerDirectQueue(index))
                    },
                )
            }

            // ── Adult — Milestone R-4 (PIN-gated at Home Screen level) ────────
            // Navigation to this route is only possible from the Home Screen
            // Adult card, which is only rendered when the PIN is unlocked.
            // A user cannot arrive here without having entered the PIN first.
            composable(Routes.ADULT) {
                AdultScreen(
                    viewModel = adultViewModel,
                    onPlayById = { videoId, queue, index, hlsMode ->
                        QueueHolder.setIdQueue(queue, index, hls = hlsMode, locked = true)
                        navController.navigate(Routes.player(videoId, index))
                    },
                    onPlayByUrl = { relativeUrl, title ->
                        QueueHolder.setUrlQueue(listOf(relativeUrl), 0)
                        navController.navigate(Routes.playerDirect(relativeUrl, title))
                    },
                    onPlayUrlQueue = { urls, index ->
                        QueueHolder.setUrlQueue(urls, index)
                        navController.navigate(Routes.playerDirectQueue(index))
                    },
                    onBack = { navController.popBackStack() },
                )
            }

            // ── EPG ──────────────────────────────────────────────────────────
            composable(Routes.EPG) {
                EpgScreen(
                    viewModel = epgViewModel,
                    onPlaySlot = { slot, channelId, offsetSeconds ->
                        // Session 42 fix: route based on slot type.
                        //
                        // WatchDawg slots have videoId set and streamUrl blank —
                        // the old URL queue approach dropped these from allUrls
                        // (takeIf { isNotBlank } filtered them out) causing index
                        // fallback to 0 which played whichever channel happened to
                        // be first in the URL list regardless of what was tapped.
                        //
                        // Fix: check videoId first. If set, route through onPlayById
                        // exactly as EpgScreen's internal handler does. Otherwise
                        // play direct URL as a single-item queue (no cross-channel
                        // queue needed — surfing handles adjacent channels itself).
                        if (slot.videoId != null) {
                            epgViewModel.setActiveChannel(channelId)
                            com.watchdawg.tv.data.prefs.QueueHolder.epgSlotStartTimeUtc = slot.startTime
                            QueueHolder.setIdQueue(listOf(slot.videoId), 0, hls = true)
                            QueueHolder.resumePositionMs = offsetSeconds * 1000L
                            QueueHolder.epgChannelNumber = slot.channelNumber
                            QueueHolder.epgChannelName   = slot.channelName
                            QueueHolder.epgSlotTitle     = slot.title
                            navController.navigate(Routes.player(slot.videoId, 0))
                        } else {
                            epgViewModel.setActiveChannel(channelId)
                            val tappedUrl = slot.streamUrl ?: ""
                            if (tappedUrl.isBlank()) return@EpgScreen
                            QueueHolder.setUrlQueue(listOf(tappedUrl), 0, isEpg = true, resumeMs = offsetSeconds * 1000L)
                            QueueHolder.epgChannelNumber = slot.channelNumber
                            QueueHolder.epgChannelName   = slot.channelName
                            QueueHolder.epgSlotTitle     = slot.title
                            navController.navigate(Routes.playerDirectQueue(0))
                        }
                    },
                    onPlayById = { videoId, hlsMode, offsetSeconds ->
                        // Session 40: WatchDawg EPG slots routed here from EpgScreen
                        // internal handler (slot.videoId != null path).
                        // Session 42: this path is now also used by onPlaySlot above
                        // for direct taps so both paths are consistent.
                        epgViewModel.setActiveChannel(videoId)
                        QueueHolder.setIdQueue(listOf(videoId), 0, hls = hlsMode)
                        QueueHolder.resumePositionMs = offsetSeconds * 1000L
                        navController.navigate(Routes.player(videoId, 0))
                    },
                    onBack = { navController.popBackStack() },
                )
            }

            // ── Settings ──────────────────────────────────────────────────────
            composable(Routes.SETTINGS) {
                SettingsScreen()
            }

            // ── Resolve-based player ──────────────────────────────────────────
            composable(
                route = Routes.PLAYER,
                arguments = listOf(
                    navArgument("videoId")    { type = NavType.IntType },
                    navArgument("startIndex") { type = NavType.IntType; defaultValue = 0 },
                ),
            ) { entry ->
                val videoId    = entry.arguments?.getInt("videoId") ?: return@composable
                val startIndex = entry.arguments?.getInt("startIndex") ?: 0
                val queue      = QueueHolder.idQueue.takeIf { it.isNotEmpty() } ?: listOf(videoId)
                val resumeMs   = QueueHolder.resumePositionMs.also { QueueHolder.resumePositionMs = 0L }
                val hlsMode    = QueueHolder.hlsMode.also { QueueHolder.hlsMode = false }
                val vm: PlayerViewModel = viewModel(factory = factory)
                PlayerScreen(
                    viewModel = vm,
                    startMode = PlayerStartMode.Resolve(videoId, queue, startIndex, resumeMs, hlsMode),
                    onExit  = {
                        Graph.playerManager(context).pause()
                        navController.popBackStack()
                    },
                    onStop  = {
                        Graph.playerManager(context).pause()
                        navController.popBackStack()
                    },
                )
            }

            // ── Direct single-URL player ──────────────────────────────────────
            composable(
                route = Routes.PLAYER_DIRECT,
                arguments = listOf(
                    navArgument("url")   { type = NavType.StringType },
                    navArgument("title") { type = NavType.StringType; defaultValue = "" },
                ),
            ) { entry ->
                val encodedUrl   = entry.arguments?.getString("url") ?: return@composable
                val encodedTitle = entry.arguments?.getString("title") ?: ""
                val url   = Routes.decode(encodedUrl)
                val title = Routes.decode(encodedTitle).ifBlank { "Now Playing" }
                val vm: PlayerViewModel = viewModel(factory = factory)
                PlayerScreen(
                    viewModel = vm,
                    startMode = PlayerStartMode.DirectSingle(url, title),
                    onExit  = {
                        Graph.playerManager(context).pause()
                        navController.popBackStack()
                    },
                    onStop  = {
                        Graph.playerManager(context).pause()
                        navController.popBackStack()
                    },
                )
            }

            // ── Direct queue player ───────────────────────────────────────────
            composable(
                route = Routes.PLAYER_DIRECT_QUEUE,
                arguments = listOf(
                    navArgument("startIndex") { type = NavType.IntType; defaultValue = 0 },
                ),
            ) { entry ->
                val startIndex = entry.arguments?.getInt("startIndex") ?: 0
                val urls = QueueHolder.urlQueue
                val isEpg = QueueHolder.isEpgQueue
                // Session 38: read resume position set by EPG onPlaySlot (offsetSeconds).
                // For non-EPG direct queues this is always 0L — no change in behaviour.
                val resumeMs = QueueHolder.resumePositionMs.also { QueueHolder.resumePositionMs = 0L }
                val vm: PlayerViewModel = viewModel(factory = factory)
                PlayerScreen(
                    viewModel = vm,
                    startMode = PlayerStartMode.DirectQueue(urls, startIndex, resumeMs),
                    onExit  = {
                        Graph.playerManager(context).pause()
                        navController.popBackStack()
                    },
                    onStop  = {
                        Graph.playerManager(context).pause()
                        navController.popBackStack()
                    },
                    // Session 38 — EPG channel surfing:
                    // Session 42 fix: surf lambdas now route each slot individually
                    // based on slot type (streamUrl vs videoId) instead of building
                    // a URL queue across all channels. The old URL queue approach
                    // silently dropped watchdawg slots (streamUrl is blank for those)
                    // causing index fallback to 0 which played the wrong channel.
                    onSurfNext = if (isEpg) ({
                        val slot = epgViewModel.getAdjacentSlot(+1)
                        if (slot != null) {
                            QueueHolder.epgChannelNumber = slot.channelNumber
                            QueueHolder.epgChannelName   = slot.channelName
                            QueueHolder.epgSlotTitle     = slot.title
                            if (slot.videoId != null) {
                                // WatchDawg slot — resolve fresh
                                com.watchdawg.tv.data.prefs.QueueHolder.epgSlotStartTimeUtc = slot.startTime
                                QueueHolder.setIdQueue(listOf(slot.videoId), 0, hls = true)
                                QueueHolder.resumePositionMs = (slot.progressSeconds ?: 0).toLong() * 1000L
                                navController.navigate(Routes.player(slot.videoId, 0)) {
                                    popUpTo(Routes.EPG) { inclusive = false }
                                }
                            } else if (!slot.streamUrl.isNullOrBlank()) {
                                // Plex / IPTV slot — play direct URL
                                QueueHolder.setUrlQueue(listOf(slot.streamUrl), 0, isEpg = true)
                                navController.navigate(Routes.playerDirectQueue(0)) {
                                    popUpTo(Routes.EPG) { inclusive = false }
                                }
                            }
                        }
                    }) else null,
                    onSurfPrev = if (isEpg) ({
                        val slot = epgViewModel.getAdjacentSlot(-1)
                        if (slot != null) {
                            QueueHolder.epgChannelNumber = slot.channelNumber
                            QueueHolder.epgChannelName   = slot.channelName
                            QueueHolder.epgSlotTitle     = slot.title
                            if (slot.videoId != null) {
                                // WatchDawg slot — resolve fresh
                                com.watchdawg.tv.data.prefs.QueueHolder.epgSlotStartTimeUtc = slot.startTime
                                QueueHolder.setIdQueue(listOf(slot.videoId), 0, hls = true)
                                QueueHolder.resumePositionMs = (slot.progressSeconds ?: 0).toLong() * 1000L
                                navController.navigate(Routes.player(slot.videoId, 0)) {
                                    popUpTo(Routes.EPG) { inclusive = false }
                                }
                            } else if (!slot.streamUrl.isNullOrBlank()) {
                                // Plex / IPTV slot — play direct URL
                                QueueHolder.setUrlQueue(listOf(slot.streamUrl), 0, isEpg = true)
                                navController.navigate(Routes.playerDirectQueue(0)) {
                                    popUpTo(Routes.EPG) { inclusive = false }
                                }
                            }
                        }
                    }) else null,
                )
            }
        }

        // ── PIN pad overlay ───────────────────────────────────────────────────
        if (showPinPad) {
            PinPadOverlay(
                viewModel = pinViewModel,
                onDismiss = { _ ->
                    showPinPad = false
                },
            )
        }

        // ── Exit confirmation dialog ──────────────────────────────────────────
        if (showExitDialog) {
            ExitConfirmDialog(
                onConfirm = { onFinish() },
                onDismiss = { showExitDialog = false },
            )
        }
    }
}

// ── Exit confirmation dialog ──────────────────────────────────────────────────

@Composable
private fun ExitConfirmDialog(
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val cancelFocus = remember { FocusRequester() }
    val closeFocus  = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        try { cancelFocus.requestFocus() } catch (_: Exception) {}
    }

    BackHandler { onDismiss() }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xCC000000))
            .onKeyEvent { event ->
                if (event.key == Key.DirectionCenter || event.key == Key.Enter)
                    return@onKeyEvent false
                when (event.key) {
                    Key.DirectionLeft  -> try { cancelFocus.requestFocus() } catch (_: Exception) {}
                    Key.DirectionRight -> try { closeFocus.requestFocus()  } catch (_: Exception) {}
                    else               -> try { cancelFocus.requestFocus() } catch (_: Exception) {}
                }
                true
            },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(24.dp),
            modifier = Modifier
                .background(WatchDawgColors.Surface, MaterialTheme.shapes.large)
                .padding(horizontal = 56.dp, vertical = 40.dp),
        ) {
            Text(
                text  = "Close WatchDawg?",
                style = MaterialTheme.typography.titleLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Button(
                    onClick = onDismiss,
                    colors  = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.Surface,
                        contentColor          = WatchDawgColors.TextSecondary,
                        focusedContainerColor = WatchDawgColors.SurfaceFocused,
                        focusedContentColor   = WatchDawgColors.TextPrimary,
                    ),
                    modifier = Modifier.width(140.dp).focusRequester(cancelFocus),
                ) {
                    Text("Cancel", style = MaterialTheme.typography.titleSmall)
                }
                Button(
                    onClick = onConfirm,
                    colors  = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.FailedBadge,
                        contentColor          = Color.White,
                        focusedContainerColor = WatchDawgColors.FailedBadge,
                        focusedContentColor   = Color.White,
                    ),
                    modifier = Modifier.width(140.dp).focusRequester(closeFocus),
                ) {
                    Text("Exit", style = MaterialTheme.typography.titleSmall)
                }
            }
        }
    }
}
