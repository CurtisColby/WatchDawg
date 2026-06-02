package com.watchdawg.tv.ui.adult

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.Card
import androidx.tv.material3.CardDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil.compose.AsyncImage
import com.watchdawg.tv.Graph
import com.watchdawg.tv.data.api.FavoriteDto
import com.watchdawg.tv.data.api.LibraryFileDto
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import com.watchdawg.tv.ui.theme.focusGlowCard
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Adult screen — Milestone R-4.
 *
 * Single home for ALL PIN-locked content.
 *
 * Pill bar (left → right):
 *   [★ Favorites] [📁 Local] [backend genre tags…]
 *
 *   Favorites → vertical list of locked-channel favorites + Remove pill per row
 *   Local     → vertical list of Private library files + Remove pill per row
 *               Remove shows "This will delete from disc. Are you sure?" dialog
 *   Genre pills → 4-column square grid of backend adult feed
 *
 * Play All / Shuffle All always operate on ALL matching content.
 */
@Composable
fun AdultScreen(
    viewModel: AdultViewModel,
    onPlayById: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    onPlayByUrl: (url: String, title: String) -> Unit,
    onPlayUrlQueue: (queue: List<String>, startIndex: Int) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val adultState      by viewModel.adultState.collectAsStateWithLifecycle()
    val genreState      by viewModel.genreState.collectAsStateWithLifecycle()
    val selectedPill    by viewModel.selectedPill.collectAsStateWithLifecycle()
    val lockedFavorites by viewModel.lockedFavorites.collectAsStateWithLifecycle()
    val removingFavIds  by viewModel.removingFavIds.collectAsStateWithLifecycle()
    val privateFiles    by viewModel.privateFiles.collectAsStateWithLifecycle()
    val removingPaths   by viewModel.removingPaths.collectAsStateWithLifecycle()
    val pendingIdQueue  by viewModel.pendingIdQueue.collectAsStateWithLifecycle()
    val pendingUrlQueue by viewModel.pendingUrlQueue.collectAsStateWithLifecycle()

    val gridState = rememberLazyGridState()

    var pendingPlay        by remember { mutableStateOf<VideoDto?>(null) }
    var pendingDeletePath  by remember { mutableStateOf<String?>(null) }
    val playFocusRequester = remember { FocusRequester() }
    val firstItemFocus     = remember { FocusRequester() }

    val scope = rememberCoroutineScope()
    var thumbGenerating by remember { mutableStateOf(false) }
    var thumbResult     by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) { viewModel.loadAll() }

    LaunchedEffect(pendingIdQueue) {
        val q = pendingIdQueue ?: return@LaunchedEffect
        if (q.ids.isNotEmpty()) {
            onPlayById(q.ids[q.startIndex], q.ids, q.startIndex, true)
            viewModel.clearPendingIdQueue()
        }
    }

    LaunchedEffect(pendingUrlQueue) {
        val q = pendingUrlQueue ?: return@LaunchedEffect
        if (q.isNotEmpty()) {
            onPlayUrlQueue(q, 0)
            viewModel.clearPendingUrlQueue()
        }
    }

    LaunchedEffect(adultState, selectedPill, privateFiles, lockedFavorites) {
        val hasContent = when (selectedPill) {
            AdultViewModel.FAVORITES_PILL -> lockedFavorites.isNotEmpty()
            AdultViewModel.LOCAL_PILL     -> privateFiles.isNotEmpty()
            else -> adultState is AdultViewModel.AdultState.Ready &&
                    (adultState as AdultViewModel.AdultState.Ready).videos.isNotEmpty()
        }
        if (hasContent) {
            delay(150)
            try { firstItemFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    LaunchedEffect(pendingPlay) {
        if (pendingPlay != null) {
            try { playFocusRequester.requestFocus() } catch (_: Exception) {}
        }
    }

    val backendTags = (genreState as? AdultViewModel.GenreState.Ready)?.tags ?: emptyList()

    val countLabel = when (selectedPill) {
        AdultViewModel.FAVORITES_PILL -> "${lockedFavorites.size} favorites"
        AdultViewModel.LOCAL_PILL     -> "${privateFiles.size} local files"
        else -> when (val s = adultState) {
            is AdultViewModel.AdultState.Ready   -> "${s.videos.size} videos"
            is AdultViewModel.AdultState.Loading -> "Loading…"
            else                                 -> "Error"
        }
    }

    val hasContent = when (selectedPill) {
        AdultViewModel.FAVORITES_PILL -> lockedFavorites.isNotEmpty()
        AdultViewModel.LOCAL_PILL     -> privateFiles.isNotEmpty()
        else -> adultState is AdultViewModel.AdultState.Ready &&
                (adultState as AdultViewModel.AdultState.Ready).videos.isNotEmpty()
    }

    Box(modifier = modifier.fillMaxSize()) {

        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(WatchDawgColors.Background)
                .padding(horizontal = 24.dp),
        ) {
            Spacer(Modifier.height(16.dp))

            // ── Header ────────────────────────────────────────────────────────
            Row(
                verticalAlignment     = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier              = Modifier.fillMaxWidth(),
            ) {
                Column {
                    Text("🔞  Adult", style = MaterialTheme.typography.displayLarge, color = WatchDawgColors.TextPrimary)
                    Text(countLabel,  style = MaterialTheme.typography.bodyLarge,    color = WatchDawgColors.TextTertiary)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (hasContent) {
                        Button(
                            onClick  = { viewModel.playAll() },
                            colors   = ButtonDefaults.colors(
                                containerColor        = WatchDawgColors.OrangeDim,
                                contentColor          = WatchDawgColors.Orange,
                                focusedContainerColor = WatchDawgColors.Orange,
                                focusedContentColor   = WatchDawgColors.Background,
                            ),
                            modifier = Modifier.focusGlow(),
                        ) { Text("▶  Play All", style = MaterialTheme.typography.titleSmall) }
                        Button(
                            onClick  = { viewModel.smartShuffle() },
                            colors   = ButtonDefaults.colors(
                                containerColor        = WatchDawgColors.OrangeDim,
                                contentColor          = WatchDawgColors.Orange,
                                focusedContainerColor = WatchDawgColors.Orange,
                                focusedContentColor   = WatchDawgColors.Background,
                            ),
                            modifier = Modifier.focusGlow(),
                        ) { Text("🎲  Smart Shuffle", style = MaterialTheme.typography.titleSmall) }
                    }
                    // Generate Thumbnails — shown when Local pill is active
                    if (selectedPill == AdultViewModel.LOCAL_PILL) {
                        Button(
                            onClick  = {
                                if (!thumbGenerating) {
                                    thumbGenerating = true
                                    thumbResult     = null
                                    scope.launch {
                                        Graph.repository.generateLocalThumbnails()
                                            .onSuccess { msg -> thumbResult = "✓  $msg" }
                                            .onFailure { thumbResult = "Failed — check server logs" }
                                        thumbGenerating = false
                                    }
                                }
                            },
                            enabled  = !thumbGenerating,
                            colors   = ButtonDefaults.colors(
                                containerColor         = WatchDawgColors.Surface,
                                contentColor           = WatchDawgColors.TextSecondary,
                                focusedContainerColor  = WatchDawgColors.SurfaceFocused,
                                focusedContentColor    = WatchDawgColors.TextPrimary,
                                disabledContainerColor = WatchDawgColors.Surface,
                                disabledContentColor   = WatchDawgColors.TextTertiary,
                            ),
                            modifier = Modifier.focusGlow(),
                        ) {
                            Text(
                                text  = if (thumbGenerating) "⏳  Generating…" else "🖼  Gen Thumbnails",
                                style = MaterialTheme.typography.titleSmall,
                            )
                        }
                    }
                }
                // Show thumbnail result feedback below header when present
                if (thumbResult != null && selectedPill == AdultViewModel.LOCAL_PILL) {
                    Text(
                        text     = thumbResult!!,
                        style    = MaterialTheme.typography.bodySmall,
                        color    = if (thumbResult!!.startsWith("✓")) WatchDawgColors.ResolvedBadge else WatchDawgColors.FailedBadge,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }

            Spacer(Modifier.height(8.dp))

            // ── Pill bar ──────────────────────────────────────────────────────
            AdultPillBar(
                backendTags   = backendTags,
                showLocalPill = privateFiles.isNotEmpty(),
                selectedPill  = selectedPill,
                onSelectPill  = { viewModel.selectPill(it) },
            )

            Spacer(Modifier.height(12.dp))

            // ── Content ───────────────────────────────────────────────────────
            when (selectedPill) {

                // ── Favorites pill — vertical list with Remove ────────────────
                AdultViewModel.FAVORITES_PILL -> {
                    if (lockedFavorites.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text("No locked favorites yet.", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextTertiary)
                        }
                    } else {
                        LazyColumn(
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                            contentPadding      = PaddingValues(bottom = 48.dp),
                            modifier            = Modifier.fillMaxSize(),
                        ) {
                            items(lockedFavorites, key = { it.videoId ?: it.hashCode() }) { fav ->
                                val idx = lockedFavorites.indexOf(fav)
                                AdultFavoriteRow(
                                    fav        = fav,
                                    isRemoving = removingFavIds.contains(fav.id),
                                    onClick    = {
                                        val vid = fav.videoId
                                        when {
                                            fav.downloadStatus == "complete" && !fav.streamUrl.isNullOrBlank() ->
                                                onPlayByUrl(fav.streamUrl, fav.title ?: "Now Playing")
                                            vid != null -> {
                                                val queue = lockedFavorites.mapNotNull { it.videoId }.filter { it > 0 }
                                                onPlayById(vid, queue, queue.indexOf(vid).coerceAtLeast(0), true)
                                            }
                                            else -> {}
                                        }
                                    },
                                    onRemove   = { fav.id?.let { viewModel.removeLockedFavorite(it) } },
                                    modifier   = if (idx == 0) Modifier.focusRequester(firstItemFocus) else Modifier,
                                )
                            }
                        }
                    }
                }

                // ── Local pill — vertical list with Remove + delete confirm ───
                AdultViewModel.LOCAL_PILL -> {
                    if (privateFiles.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text("No local adult files downloaded yet.", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextTertiary)
                        }
                    } else {
                        LazyColumn(
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                            contentPadding      = PaddingValues(bottom = 48.dp),
                            modifier            = Modifier.fillMaxSize(),
                        ) {
                            items(privateFiles, key = { it.relativePath ?: it.filename ?: it.hashCode().toString() }) { file ->
                                val idx = privateFiles.indexOf(file)
                                AdultLocalRow(
                                    file       = file,
                                    isRemoving = removingPaths.contains(file.relativePath),
                                    onClick    = {
                                        val url = file.streamUrl
                                        if (!url.isNullOrBlank()) onPlayByUrl(url, file.title ?: file.filename ?: "Now Playing")
                                    },
                                    onRemove   = { pendingDeletePath = file.relativePath },
                                    modifier   = if (idx == 0) Modifier.focusRequester(firstItemFocus) else Modifier,
                                )
                            }
                        }
                    }
                }

                // ── Genre / All pill — 4-column square grid ───────────────────
                else -> {
                    when (val s = adultState) {
                        is AdultViewModel.AdultState.Loading -> {
                            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                Text("Loading…", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextTertiary)
                            }
                        }
                        is AdultViewModel.AdultState.Error -> {
                            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                Text("Could not load content: ${s.message}", style = MaterialTheme.typography.titleMedium, color = WatchDawgColors.FailedBadge)
                            }
                        }
                        is AdultViewModel.AdultState.Ready -> {
                            if (s.videos.isEmpty()) {
                                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                    Text(
                                        if (selectedPill != null) "No videos for \"$selectedPill\"" else "No adult content yet.",
                                        style = MaterialTheme.typography.titleLarge,
                                        color = WatchDawgColors.TextTertiary,
                                    )
                                }
                            } else {
                                LazyVerticalGrid(
                                    columns               = GridCells.Fixed(4),
                                    state                 = gridState,
                                    contentPadding        = PaddingValues(bottom = 48.dp),
                                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                                    verticalArrangement   = Arrangement.spacedBy(12.dp),
                                    modifier              = Modifier.fillMaxSize(),
                                ) {
                                    items(s.videos, key = { it.id }) { video ->
                                        val idx = s.videos.indexOf(video)
                                        AdultStreamCard(
                                            video    = video,
                                            onPlay   = { pendingPlay = video },
                                            modifier = if (idx == 0) Modifier.focusRequester(firstItemFocus) else Modifier,
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // ── Play mode menu (backend stream content) ────────────────────────────
        if (pendingPlay != null) {
            val video = pendingPlay!!
            AdultPlayModeMenu(
                onPlay = { hlsMode ->
                    val videos = (adultState as? AdultViewModel.AdultState.Ready)?.videos ?: emptyList()
                    val ids    = videos.map { it.id }
                    val index  = ids.indexOf(video.id).coerceAtLeast(0)
                    pendingPlay = null
                    onPlayById(video.id, ids, index, hlsMode)
                },
                onDismiss          = { pendingPlay = null },
                playFocusRequester = playFocusRequester,
            )
        }

        // ── Delete file confirmation dialog (Local pill) ───────────────────────
        if (pendingDeletePath != null) {
            val path = pendingDeletePath!!
            DeleteFileConfirmDialog(
                onConfirm = {
                    pendingDeletePath = null
                    viewModel.removePrivateFile(path)
                },
                onDismiss = { pendingDeletePath = null },
            )
        }
    }
}

// ── Pill bar ──────────────────────────────────────────────────────────────────

@Composable
private fun AdultPillBar(
    backendTags: List<String>,
    showLocalPill: Boolean,
    selectedPill: String?,
    onSelectPill: (String?) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyRow(
        state                 = rememberLazyListState(),
        modifier              = modifier.height(48.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment     = Alignment.CenterVertically,
        contentPadding        = PaddingValues(horizontal = 4.dp),
    ) {
        item(key = AdultViewModel.FAVORITES_PILL) {
            AdultPill(
                label      = "★  Favorites",
                isSelected = selectedPill == AdultViewModel.FAVORITES_PILL,
                onClick    = { onSelectPill(AdultViewModel.FAVORITES_PILL) },
            )
        }
        if (showLocalPill) {
            item(key = AdultViewModel.LOCAL_PILL) {
                AdultPill(
                    label      = "📁  Local",
                    isSelected = selectedPill == AdultViewModel.LOCAL_PILL,
                    onClick    = { onSelectPill(AdultViewModel.LOCAL_PILL) },
                )
            }
        }
        items(backendTags, key = { it }) { tag ->
            AdultPill(
                label      = tag,
                isSelected = selectedPill == tag,
                onClick    = { onSelectPill(tag) },
            )
        }
    }
}

@Composable
private fun AdultPill(
    label: String,
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Button(
        onClick  = onClick,
        colors   = ButtonDefaults.colors(
            containerColor        = if (isSelected) WatchDawgColors.Orange else WatchDawgColors.OrangeDim,
            contentColor          = if (isSelected) WatchDawgColors.Background else WatchDawgColors.Orange,
            focusedContainerColor = WatchDawgColors.Orange,
            focusedContentColor   = WatchDawgColors.Background,
        ),
        modifier = modifier.focusGlow(),
    ) {
        Text(text = label, style = MaterialTheme.typography.labelLarge)
    }
}

// ── Locked favorite row (with Remove pill) ────────────────────────────────────

@Composable
private fun AdultFavoriteRow(
    fav: FavoriteDto,
    isRemoving: Boolean,
    onClick: () -> Unit,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var cardHasFocus by remember { mutableStateOf(false) }

    val thumbUrl = fav.thumbnailUrl?.let { url ->
        if (url.startsWith("/")) Graph.serverPrefs.getBaseUrl().trimEnd('/') + url else url
    }

    Row(
        modifier              = modifier.fillMaxWidth(),
        verticalAlignment     = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card(
            onClick  = onClick,
            colors   = CardDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
            ),
            scale    = CardDefaults.scale(focusedScale = 1.02f),
            modifier = Modifier
                .weight(1f)
                .focusGlowCard(cardHasFocus)
                .onFocusChanged { cardHasFocus = it.isFocused },
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(8.dp)) {
                Box(
                    modifier = Modifier
                        .width(160.dp)
                        .aspectRatio(16f / 9f)
                        .clip(MaterialTheme.shapes.small),
                ) {
                    AsyncImage(
                        model              = thumbUrl,
                        contentDescription = fav.title,
                        contentScale       = ContentScale.Crop,
                        modifier           = Modifier.fillMaxSize().background(WatchDawgColors.SurfaceElevated),
                    )
                    // 🔞 badge
                    Box(
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .padding(4.dp)
                            .clip(RoundedCornerShape(4.dp))
                            .background(Color(0xCC000000))
                            .padding(horizontal = 4.dp, vertical = 1.dp),
                    ) { Text(text = "🔞", style = MaterialTheme.typography.labelSmall) }
                }
                Spacer(Modifier.width(16.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(fav.title ?: "Untitled", style = MaterialTheme.typography.titleMedium, color = WatchDawgColors.TextPrimary, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (!fav.artist.isNullOrBlank()) {
                        Text(fav.artist, style = MaterialTheme.typography.bodyMedium, color = WatchDawgColors.Orange, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 2.dp))
                    }
                    if (!fav.channelName.isNullOrBlank()) {
                        Text(fav.channelName, style = MaterialTheme.typography.bodySmall, color = WatchDawgColors.TextTertiary, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        Button(
            onClick  = { if (!isRemoving) onRemove() },
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                contentColor          = WatchDawgColors.TextSecondary,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
                focusedContentColor   = WatchDawgColors.FailedBadge,
            ),
            modifier = Modifier.width(110.dp).focusGlow(),
        ) {
            Text(if (isRemoving) "…" else "✕  Remove", style = MaterialTheme.typography.labelLarge)
        }
    }
}

// ── Private local file row (with Remove pill) ─────────────────────────────────

@Composable
private fun AdultLocalRow(
    file: LibraryFileDto,
    isRemoving: Boolean,
    onClick: () -> Unit,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var cardHasFocus by remember { mutableStateOf(false) }

    val thumbUrl = file.thumbnailUrl?.let { url ->
        if (url.startsWith("/")) Graph.serverPrefs.getBaseUrl().trimEnd('/') + url else url
    }

    Row(
        modifier              = modifier.fillMaxWidth(),
        verticalAlignment     = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card(
            onClick  = onClick,
            colors   = CardDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
            ),
            scale    = CardDefaults.scale(focusedScale = 1.02f),
            modifier = Modifier
                .weight(1f)
                .focusGlowCard(cardHasFocus)
                .onFocusChanged { cardHasFocus = it.isFocused },
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(8.dp)) {
                Box(
                    modifier = Modifier
                        .width(160.dp)
                        .aspectRatio(16f / 9f)
                        .clip(MaterialTheme.shapes.small),
                ) {
                    AsyncImage(
                        model              = thumbUrl,
                        contentDescription = file.filename,
                        contentScale       = ContentScale.Crop,
                        modifier           = Modifier.fillMaxSize().background(WatchDawgColors.SurfaceElevated),
                    )
                    Box(
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .padding(4.dp)
                            .clip(RoundedCornerShape(4.dp))
                            .background(Color(0xCC000000))
                            .padding(horizontal = 4.dp, vertical = 1.dp),
                    ) { Text(text = "🔞", style = MaterialTheme.typography.labelSmall) }
                }
                Spacer(Modifier.width(16.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(file.title ?: file.filename ?: "Untitled", style = MaterialTheme.typography.titleMedium, color = WatchDawgColors.TextPrimary, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (!file.sizeHuman.isNullOrBlank()) {
                        Text(file.sizeHuman, style = MaterialTheme.typography.bodySmall, color = WatchDawgColors.TextTertiary, maxLines = 1, modifier = Modifier.padding(top = 2.dp))
                    }
                }
            }
        }
        Button(
            onClick  = { if (!isRemoving) onRemove() },
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                contentColor          = WatchDawgColors.TextSecondary,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
                focusedContentColor   = WatchDawgColors.FailedBadge,
            ),
            modifier = Modifier.width(110.dp).focusGlow(),
        ) {
            Text(if (isRemoving) "…" else "✕  Remove", style = MaterialTheme.typography.labelLarge)
        }
    }
}

// ── Delete file confirmation dialog ───────────────────────────────────────────

@Composable
private fun DeleteFileConfirmDialog(
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val cancelFocus  = remember { FocusRequester() }
    val confirmFocus = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        try { cancelFocus.requestFocus() } catch (_: Exception) {}
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xCC000000))
            .onKeyEvent { event ->
                if (event.type == KeyEventType.KeyUp && event.key == Key.Back) {
                    onDismiss(); true
                } else false
            },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(24.dp),
            modifier = Modifier
                .background(WatchDawgColors.Surface, RoundedCornerShape(16.dp))
                .padding(horizontal = 56.dp, vertical = 40.dp),
        ) {
            Text("🗑  Delete File?", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextPrimary)
            Text(
                "This will permanently delete the file from disc.\nAre you sure?",
                style = MaterialTheme.typography.bodyMedium,
                color = WatchDawgColors.TextSecondary,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Button(
                    onClick  = onDismiss,
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.Surface,
                        contentColor          = WatchDawgColors.TextSecondary,
                        focusedContainerColor = WatchDawgColors.SurfaceFocused,
                        focusedContentColor   = WatchDawgColors.TextPrimary,
                    ),
                    modifier = Modifier.width(140.dp).focusRequester(cancelFocus).focusGlow(),
                ) { Text("Cancel", style = MaterialTheme.typography.titleSmall) }
                Button(
                    onClick  = onConfirm,
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.FailedBadge,
                        contentColor          = Color.White,
                        focusedContainerColor = WatchDawgColors.FailedBadge,
                        focusedContentColor   = Color.White,
                    ),
                    modifier = Modifier.width(140.dp).focusRequester(confirmFocus).focusGlow(),
                ) { Text("Delete", style = MaterialTheme.typography.titleSmall) }
            }
        }
    }
}

// ── Play mode menu ────────────────────────────────────────────────────────────

@Composable
private fun AdultPlayModeMenu(
    onPlay: (hlsMode: Boolean) -> Unit,
    onDismiss: () -> Unit,
    playFocusRequester: FocusRequester,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xBB000000))
            .onKeyEvent { event ->
                if (event.type == KeyEventType.KeyUp && event.key == Key.Back) {
                    onDismiss(); true
                } else false
            },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .width(440.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(WatchDawgColors.Surface)
                .padding(horizontal = 32.dp, vertical = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text("Choose Play Mode", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextPrimary)
            Spacer(Modifier.height(4.dp))
            Button(
                onClick  = { onPlay(true) },
                colors   = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.OrangeDim,
                    contentColor          = WatchDawgColors.Orange,
                    focusedContainerColor = WatchDawgColors.Orange,
                    focusedContentColor   = WatchDawgColors.Background,
                ),
                modifier = Modifier.fillMaxWidth().focusRequester(playFocusRequester).focusGlow(),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("⚡  HLS Mode", style = MaterialTheme.typography.titleMedium)
                    Text("Seekable · Recommended", style = MaterialTheme.typography.labelMedium, color = WatchDawgColors.TextSecondary)
                }
            }
            Button(
                onClick  = { onPlay(false) },
                colors   = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.Surface,
                    contentColor          = WatchDawgColors.TextSecondary,
                    focusedContainerColor = WatchDawgColors.SurfaceFocused,
                    focusedContentColor   = WatchDawgColors.TextPrimary,
                ),
                modifier = Modifier.fillMaxWidth().focusGlow(),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("▶  Split Stream", style = MaterialTheme.typography.titleMedium)
                    Text("Best quality · No seeking · HLS fallback", style = MaterialTheme.typography.labelMedium, color = WatchDawgColors.TextTertiary)
                }
            }
            Button(
                onClick  = onDismiss,
                colors   = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.Surface,
                    contentColor          = WatchDawgColors.TextTertiary,
                    focusedContainerColor = WatchDawgColors.SurfaceFocused,
                    focusedContentColor   = WatchDawgColors.TextSecondary,
                ),
                modifier = Modifier.fillMaxWidth().focusGlow(),
            ) { Text("✕  Cancel", style = MaterialTheme.typography.titleMedium) }
        }
    }
}
