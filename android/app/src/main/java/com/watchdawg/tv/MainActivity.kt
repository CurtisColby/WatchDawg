package com.watchdawg.tv

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
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
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
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
import com.watchdawg.tv.ui.auth.PinPadOverlay
import com.watchdawg.tv.ui.auth.PinViewModel
import com.watchdawg.tv.ui.auth.globalUnlockGesture
import com.watchdawg.tv.ui.channels.ChannelsScreen
import com.watchdawg.tv.ui.continuewatching.ContinueWatchingScreen
import com.watchdawg.tv.ui.continuewatching.ContinueWatchingViewModel
import com.watchdawg.tv.ui.favorites.FavoritesScreen
import com.watchdawg.tv.ui.feed.FeedScreen
import com.watchdawg.tv.ui.feed.FeedViewModel
import com.watchdawg.tv.ui.library.FavoritesViewModel
import com.watchdawg.tv.ui.library.LibraryScreen
import com.watchdawg.tv.ui.library.LibraryViewModel
import com.watchdawg.tv.ui.nav.NavRail
import com.watchdawg.tv.ui.nav.NavSection
import com.watchdawg.tv.ui.nav.Routes
import com.watchdawg.tv.ui.player.PlayerScreen
import com.watchdawg.tv.ui.player.PlayerStartMode
import com.watchdawg.tv.ui.player.PlayerViewModel
import com.watchdawg.tv.ui.series.EpisodeListScreen
import com.watchdawg.tv.ui.series.SeriesViewModel
import com.watchdawg.tv.ui.settings.SettingsScreen
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.WatchDawgTheme
import com.watchdawg.tv.ui.watchlater.WatchLaterScreen
import com.watchdawg.tv.ui.watchlater.WatchLaterViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
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
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        Graph.playerManagerIfExists()?.reattachSurface()
    }

    override fun onPause() {
        super.onPause()
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    override fun onStop() {
        super.onStop()
        Graph.playerManagerIfExists()?.detachSurface()
    }

    override fun onDestroy() {
        super.onDestroy()
        TokenHolder.clear()
        QueueHolder.clear()
        Graph.releasePlayerManager()
    }
}

@OptIn(UnstableApi::class)
@Composable
private fun WatchDawgRoot(onFinish: () -> Unit) {
    val context = LocalContext.current
    val navController = rememberNavController()
    val factory = remember { WatchDawgViewModelFactory() }
    val feedViewModel: FeedViewModel = viewModel(factory = factory)
    val pinViewModel: PinViewModel = viewModel(factory = factory)
    // Milestone F: SeriesViewModel is hoisted here so it survives pill switches
    // and EpisodeListScreen navigations without reloading the series list.
    val seriesViewModel: SeriesViewModel = viewModel(factory = factory)

    var showPinPad by remember { mutableStateOf(false) }
    var showExitDialog by remember { mutableStateOf(false) }

    var miniPlayerActive by remember { mutableStateOf(false) }
    var miniVideoId by remember { mutableIntStateOf(-1) }
    var miniStartIndex by remember { mutableIntStateOf(0) }
    val miniPlayerFocus = remember { FocusRequester() }

    val backStack by navController.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route ?: Routes.FEED
    val onPlayer = currentRoute.startsWith("player")

    LaunchedEffect(onPlayer) {
        if (!onPlayer) {
            val pm = Graph.playerManager(context)
            if (pm.player.playbackState != 1) {
                miniPlayerActive = true
                miniVideoId = QueueHolder.idQueue.getOrNull(QueueHolder.startIndex) ?: -1
                miniStartIndex = QueueHolder.startIndex
            }
        }
    }

    LaunchedEffect(miniPlayerActive) {
        if (miniPlayerActive) {
            try { miniPlayerFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    // Bug 2 fix (expanded scope): when the mini-player is dismissed via Back,
    // we pause + clear miniPlayerActive AND immediately navigate to Feed with a
    // full back-stack pop. This covers every launch path.
    BackHandler(enabled = !onPlayer && !showPinPad && !showExitDialog && miniPlayerActive) {
        Graph.playerManager(context).pause()
        miniPlayerActive = false
        navController.navigate(Routes.FEED) {
            launchSingleTop = true
            popUpTo(Routes.FEED) { inclusive = false }
        }
    }

    BackHandler(enabled = !onPlayer && !showPinPad && !showExitDialog && !miniPlayerActive) {
        showExitDialog = true
    }

    fun expandToFullScreen() {
        val videoId = miniVideoId
        val queue = QueueHolder.idQueue
        val index = miniStartIndex
        val positionMs = Graph.playerManager(context).positionMs()
        QueueHolder.resumePositionMs = positionMs
        miniPlayerActive = false
        if (videoId > 0 && queue.isNotEmpty()) {
            navController.navigate(Routes.player(videoId, index))
        }
    }

    // Helper: navigate to a top-level section, stopping mini-player
    fun navigateTo(section: NavSection) {
        miniPlayerActive = false
        Graph.playerManager(context).pause()
        navController.navigate(section.route) {
            launchSingleTop = true
            restoreState = true
            popUpTo(Routes.FEED) { saveState = true }
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WatchDawgColors.Background)
            .globalUnlockGesture(enabled = !showPinPad) { showPinPad = true },
    ) {
        Row(Modifier.fillMaxSize()) {

            if (!onPlayer) {
                NavRail(
                    current = currentRoute,
                    onSelect = { section -> navigateTo(section) },
                )
            }

            Box(modifier = Modifier.fillMaxSize()) {

                // ── Mini-player ───────────────────────────────────────────────
                if (!onPlayer && miniPlayerActive) {
                    AndroidView(
                        factory = { ctx ->
                            PlayerView(ctx).apply {
                                player = Graph.playerManager(ctx).player
                                useController = false
                                resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                            }
                        },
                        modifier = Modifier
                            .fillMaxSize()
                            .focusRequester(miniPlayerFocus)
                            .focusable()
                            .onKeyEvent { event ->
                                if (event.type == KeyEventType.KeyUp) {
                                    when (event.key) {
                                        Key.DirectionRight,
                                        Key.DirectionCenter,
                                        Key.Enter -> {
                                            expandToFullScreen()
                                            true
                                        }
                                        else -> false
                                    }
                                } else false
                            },
                    )
                }

                // ── Nav host ──────────────────────────────────────────────────
                NavHost(
                    navController = navController,
                    startDestination = Routes.FEED,
                    modifier = Modifier.fillMaxSize().then(
                        if (!onPlayer && miniPlayerActive)
                            Modifier.background(Color.Transparent)
                        else Modifier
                    ),
                ) {
                    // ── Feed ──────────────────────────────────────────────────
                    composable(Routes.FEED) {
                        if (!miniPlayerActive) {
                            FeedScreen(
                                viewModel       = feedViewModel,
                                seriesViewModel = seriesViewModel,
                                onPlay = { videoId, queue, index, hlsMode ->
                                    miniPlayerActive = false
                                    QueueHolder.setIdQueue(queue, index, hls = hlsMode)
                                    navController.navigate(Routes.player(videoId, index))
                                },
                                onResumePlay = { videoId, queue, index, positionMs ->
                                    miniPlayerActive = false
                                    QueueHolder.setIdQueue(queue, index)
                                    QueueHolder.resumePositionMs = positionMs
                                    navController.navigate(Routes.player(videoId, index))
                                },
                                // Milestone F: tap a series card → navigate to episode list
                                onSeriesTap = { channelId, channelName ->
                                    navController.navigate(
                                        Routes.episodeList(channelId, channelName)
                                    )
                                },
                            )
                        }
                    }

                    // ── Milestone F: Episode list drill-down ──────────────────
                    composable(
                        route = Routes.EPISODE_LIST,
                        arguments = listOf(
                            navArgument("channelId") { type = NavType.IntType },
                            navArgument("channelName") { type = NavType.StringType },
                        ),
                    ) { entry ->
                        val channelId = entry.arguments?.getInt("channelId") ?: return@composable
                        val encodedName = entry.arguments?.getString("channelName") ?: ""
                        val channelName = Routes.decode(encodedName).ifBlank { "TV Series" }
                        EpisodeListScreen(
                            channelId    = channelId,
                            channelName  = channelName,
                            viewModel    = seriesViewModel,
                            onPlay = { videoId, queue, index, hlsMode ->
                                miniPlayerActive = false
                                QueueHolder.setIdQueue(queue, index, hls = hlsMode)
                                navController.navigate(Routes.player(videoId, index))
                            },
                            onBack = { navController.popBackStack() },
                        )
                    }

                    // ── Continue Watching ─────────────────────────────────────
                    composable(Routes.CONTINUE_WATCHING) {
                        if (!miniPlayerActive) {
                            val vm: ContinueWatchingViewModel = viewModel(factory = factory)
                            ContinueWatchingScreen(
                                viewModel = vm,
                                onResumePlay = { videoId, queue, index, positionMs ->
                                    miniPlayerActive = false
                                    QueueHolder.setIdQueue(queue, index, hls = true)
                                    QueueHolder.resumePositionMs = positionMs
                                    navController.navigate(Routes.player(videoId, index))
                                },
                            )
                        }
                    }

                    // ── Watch Later ───────────────────────────────────────────
                    composable(Routes.WATCH_LATER) {
                        val vm: WatchLaterViewModel = viewModel(factory = factory)
                        WatchLaterScreen(
                            viewModel = vm,
                            onPlay = { videoId, queue, index, hlsMode ->
                                miniPlayerActive = false
                                QueueHolder.setIdQueue(queue, index, hls = hlsMode)
                                navController.navigate(Routes.player(videoId, index))
                            },
                        )
                    }

                    // ── Channels ──────────────────────────────────────────────
                    composable(Routes.CHANNELS) {
                        ChannelsScreen(feedViewModel = feedViewModel)
                    }

                    // ── Favorites ─────────────────────────────────────────────
                    composable(Routes.FAVORITES) {
                        if (!miniPlayerActive) {
                        val vm: FavoritesViewModel = viewModel(factory = factory)
                        FavoritesScreen(
                            viewModel = vm,
                            onPlayById = { videoId, queue, index ->
                                miniPlayerActive = false
                                QueueHolder.setIdQueue(queue, index)
                                navController.navigate(Routes.player(videoId, index))
                            },
                            onPlayStreamUrl = { relativeUrl, title ->
                                miniPlayerActive = false
                                QueueHolder.setUrlQueue(listOf(relativeUrl), 0)
                                navController.navigate(Routes.playerDirect(relativeUrl, title))
                            },
                            onPlayQueue = { ids, index ->
                                miniPlayerActive = false
                                QueueHolder.setIdQueue(ids, index)
                                if (ids.isNotEmpty()) {
                                    navController.navigate(Routes.player(ids[index], index))
                                }
                            },
                        )
                        }
                    }

                    // ── Library ───────────────────────────────────────────────
                    composable(Routes.LIBRARY) {
                        if (!miniPlayerActive) {
                        val vm: LibraryViewModel = viewModel(factory = factory)
                        LibraryScreen(
                            viewModel = vm,
                            onPlayStreamUrl = { relativeUrl, title ->
                                miniPlayerActive = false
                                QueueHolder.setUrlQueue(listOf(relativeUrl), 0)
                                navController.navigate(Routes.playerDirect(relativeUrl, title))
                            },
                            onPlayQueue = { urls, index ->
                                miniPlayerActive = false
                                QueueHolder.setUrlQueue(urls, index)
                                navController.navigate(Routes.playerDirectQueue(index))
                            },
                        )
                        }
                    }

                    composable(Routes.SETTINGS) { SettingsScreen() }

                    // ── Resolve-based player ──────────────────────────────────
                    composable(
                        route = Routes.PLAYER,
                        arguments = listOf(
                            navArgument("videoId") { type = NavType.IntType },
                            navArgument("startIndex") { type = NavType.IntType; defaultValue = 0 },
                        ),
                    ) { entry ->
                        val videoId = entry.arguments?.getInt("videoId") ?: return@composable
                        val startIndex = entry.arguments?.getInt("startIndex") ?: 0
                        val queue = QueueHolder.idQueue.takeIf { it.isNotEmpty() } ?: listOf(videoId)
                        val resumeMs = QueueHolder.resumePositionMs.also { QueueHolder.resumePositionMs = 0L }
                        val hlsMode = QueueHolder.hlsMode.also { QueueHolder.hlsMode = false }
                        val vm: PlayerViewModel = viewModel(factory = factory)
                        PlayerScreen(
                            viewModel = vm,
                            startMode = PlayerStartMode.Resolve(videoId, queue, startIndex, resumeMs, hlsMode),
                            onExit = { navController.popBackStack() },
                            onStop = {
                                Graph.playerManager(context).pause()
                                miniPlayerActive = false
                                navController.popBackStack()
                            },
                        )
                    }

                    // ── Direct single-URL player ──────────────────────────────
                    composable(
                        route = Routes.PLAYER_DIRECT,
                        arguments = listOf(
                            navArgument("url") { type = NavType.StringType },
                            navArgument("title") { type = NavType.StringType; defaultValue = "" },
                        ),
                    ) { entry ->
                        val encodedUrl = entry.arguments?.getString("url") ?: return@composable
                        val encodedTitle = entry.arguments?.getString("title") ?: ""
                        val url = Routes.decode(encodedUrl)
                        val title = Routes.decode(encodedTitle).ifBlank { "Now Playing" }
                        val vm: PlayerViewModel = viewModel(factory = factory)
                        PlayerScreen(
                            viewModel = vm,
                            startMode = PlayerStartMode.DirectSingle(url, title),
                            onExit = { navController.popBackStack() },
                            onStop = {
                                Graph.playerManager(context).pause()
                                miniPlayerActive = false
                                navController.popBackStack()
                            },
                        )
                    }

                    // ── Direct queue player ───────────────────────────────────
                    composable(
                        route = Routes.PLAYER_DIRECT_QUEUE,
                        arguments = listOf(
                            navArgument("startIndex") { type = NavType.IntType; defaultValue = 0 },
                        ),
                    ) { entry ->
                        val startIndex = entry.arguments?.getInt("startIndex") ?: 0
                        val urls = QueueHolder.urlQueue
                        val vm: PlayerViewModel = viewModel(factory = factory)
                        PlayerScreen(
                            viewModel = vm,
                            startMode = PlayerStartMode.DirectQueue(urls, startIndex),
                            onExit = { navController.popBackStack() },
                            onStop = {
                                Graph.playerManager(context).pause()
                                miniPlayerActive = false
                                navController.popBackStack()
                            },
                        )
                    }
                }
            }
        }

        // ── PIN pad overlay ───────────────────────────────────────────────────
        if (showPinPad) {
            PinPadOverlay(
                viewModel = pinViewModel,
                onDismiss = { wasUnlocked ->
                    showPinPad = false
                    if (wasUnlocked) feedViewModel.onSessionUnlocked()
                    else if (TokenHolder.isUnlocked) feedViewModel.onSessionUnlocked()
                    else feedViewModel.onSessionLocked()
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
                    modifier = Modifier
                        .width(140.dp)
                        .focusRequester(cancelFocus),
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
                    modifier = Modifier
                        .width(140.dp)
                        .focusRequester(closeFocus),
                ) {
                    Text("Exit", style = MaterialTheme.typography.titleSmall)
                }
            }
        }
    }
}
