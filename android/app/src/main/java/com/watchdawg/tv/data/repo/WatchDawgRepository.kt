package com.watchdawg.tv.data.repo

import com.watchdawg.tv.data.api.ApiClient
import com.watchdawg.tv.data.api.AuthStatusResponse
import com.watchdawg.tv.data.api.ChannelDto
import com.watchdawg.tv.data.api.EpisodesResponse
import com.watchdawg.tv.data.api.FavoriteDto
import com.watchdawg.tv.data.api.FeedIdsResponse
import com.watchdawg.tv.data.api.FeedResponse
import com.watchdawg.tv.data.api.HistoryItemDto
import com.watchdawg.tv.data.api.HistoryUpdateRequest
import com.watchdawg.tv.data.api.LibraryResponse
import com.watchdawg.tv.data.api.LiveTvChannelDto
import com.watchdawg.tv.data.api.ResolveResponse
import com.watchdawg.tv.data.api.SeriesItemDto
import com.watchdawg.tv.data.api.SkipRequest
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.api.WatchlistItemDto
import com.watchdawg.tv.data.auth.TokenHolder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import retrofit2.HttpException

/**
 * Single source of truth for all backend communication.
 *
 * Session 36: toggleLiveChannelFavorite() — POST /live-tv/channels/{id}/favorite.
 *             deleteLiveChannel() — DELETE /live-tv/channels/{id} (session 35).
 */
class WatchDawgRepository(private val client: ApiClient) {

    private val api get() = client.api

    suspend fun getFeed(
        limit: Int = 1000,
        offset: Int = 0,
        channelIds: String? = null,
        status: String? = null,
        category: String? = null,
        genreTag: String? = null,
    ): Result<FeedResponse> = io {
        api.getFeed(
            limit      = limit,
            offset     = offset,
            channelIds = channelIds,
            status     = status,
            category   = category,
            genreTag   = genreTag,
        )
    }

    suspend fun getFeedIds(
        channelIds: String? = null,
        category: String? = null,
        genreTag: String? = null,
        orderBy: String? = null,
    ): Result<FeedIdsResponse> = io {
        api.getFeedIds(
            channelIds = channelIds,
            category   = category,
            genreTag   = genreTag,
            orderBy    = orderBy,
        )
    }

    sealed class ResolveOutcome {
        data class Ok(val data: ResolveResponse) : ResolveOutcome()
        object Unavailable : ResolveOutcome()
        data class Error(val throwable: Throwable) : ResolveOutcome()
    }

    suspend fun resolve(id: Int, force: Boolean = false, client: String = "tv"): ResolveOutcome =
        withContext(Dispatchers.IO) {
            try {
                ResolveOutcome.Ok(api.resolve(id = id, force = force, client = client))
            } catch (e: HttpException) {
                if (e.code() == 404) ResolveOutcome.Unavailable
                else ResolveOutcome.Error(e)
            } catch (e: Exception) {
                ResolveOutcome.Error(e)
            }
        }

    suspend fun getChannels(): Result<List<ChannelDto>> = io {
        api.getChannels().channels
    }

    suspend fun skip(videoId: Int): Result<Unit> = io {
        api.skip(SkipRequest(videoId)); Unit
    }

    suspend fun bookmark(videoId: Int): Result<Unit> = io {
        api.bookmark(videoId); Unit
    }

    suspend fun favorite(videoId: Int): Result<Unit> = io {
        api.favorite(videoId); Unit
    }

    suspend fun getFavorites(): Result<List<FavoriteDto>> = io {
        api.getFavorites().favorites
    }

    suspend fun removeFavorite(favoriteId: Int): Result<Unit> = io {
        api.removeFavorite(favoriteId); Unit
    }

    suspend fun retryFavorite(favoriteId: Int): Result<Unit> = io {
        api.retryFavorite(favoriteId); Unit
    }

    suspend fun getLibrary(genre: String? = null): Result<LibraryResponse> = io {
        api.getLibrary(genre = genre)
    }

    suspend fun getLibraryGenres(): Result<List<String>> = io {
        api.getLibraryGenres().tags
    }

    suspend fun deleteLibraryFile(relativePath: String): Result<Unit> = io {
        api.deleteLibraryFile(relativePath); Unit
    }

    suspend fun generateLocalThumbnails(limit: Int = 50): Result<String> = io {
        val resp = api.generateLocalThumbnails(limit = limit)
        resp.message ?: resp.status ?: "Thumbnails generated"
    }

    suspend fun authStatus(): Result<AuthStatusResponse> = io {
        api.authStatus()
    }

    suspend fun unlock(pin: String): Result<Boolean> = io {
        val resp = api.unlock(pin)
        val token = resp.token
        if (!token.isNullOrEmpty()) {
            TokenHolder.set(token)
            true
        } else {
            resp.status == "success" || resp.status == "unlocked"
        }
    }

    suspend fun lock(): Result<Unit> = io {
        try { api.lock() } finally { TokenHolder.clear() }
        Unit
    }

    suspend fun getWatchlist(): Result<List<WatchlistItemDto>> = io {
        api.getWatchlist().watchlist
    }

    suspend fun addToWatchlist(videoId: Int): Result<Unit> = io {
        api.addToWatchlist(videoId); Unit
    }

    suspend fun removeFromWatchlist(videoId: Int): Result<Unit> = io {
        api.removeFromWatchlist(videoId); Unit
    }

    suspend fun getHistory(limit: Int = 50): Result<List<HistoryItemDto>> = io {
        api.getHistory(limit = limit).history
    }

    suspend fun deleteHistory(videoId: Int): Result<Unit> = io {
        api.deleteHistory(videoId); Unit
    }

    suspend fun postHistory(
        videoId: Int,
        positionSeconds: Float,
        durationSeconds: Float?,
    ): Result<Unit> = io {
        api.postHistory(
            videoId = videoId,
            body    = HistoryUpdateRequest(
                positionSeconds = positionSeconds,
                durationSeconds = durationSeconds,
            ),
        )
        Unit
    }

    suspend fun fetchSeries(genreTag: String? = null): Result<List<SeriesItemDto>> = io {
        api.getSeries(genreTag = genreTag).series
    }

    suspend fun fetchEpisodes(channelId: Int): Result<EpisodesResponse> = io {
        api.getEpisodes(channelId = channelId)
    }

    suspend fun fetchGenres(category: String): Result<List<String>> = io {
        api.getGenres(category = category).tags
    }

    // ── Live TV (Milestone I / Session 36) ───────────────────────────────────

    /**
     * Fetch live TV channels. Backend excludes disabled-source channels by
     * default (include_disabled omitted = false). Confirmed-offline channels
     * (is_online == false) are filtered client-side in LiveTvViewModel.
     */
    suspend fun getLiveChannels(): Result<List<LiveTvChannelDto>> = io {
        api.getLiveChannels().channels
    }

    /**
     * Delete a single live TV channel via DELETE /live-tv/channels/{id}.
     * Called from the action sheet overlay in LiveTvScreen.
     */
    suspend fun deleteLiveChannel(id: Int): Result<Unit> = io {
        api.deleteLiveChannel(id); Unit
    }

    /**
     * Toggle the is_favorite flag on a live TV channel.
     * Returns the new Boolean state so the ViewModel can update in-memory
     * without a full reload.
     */
    suspend fun toggleLiveChannelFavorite(id: Int): Result<Boolean> = io {
        api.toggleLiveChannelFavorite(id).isFavorite
    }

    // ── Maintenance ───────────────────────────────────────────────────────────

    suspend fun scrapeAll(channelIds: String? = null): Result<String> = io {
        val resp = api.scrapeAll(limit = 2000, channelIds = channelIds)
        resp.message ?: resp.status ?: "Scrape started"
    }

    suspend fun resolveBatch(channelIds: String? = null): Result<String> = io {
        val resp = api.resolveBatch(limit = 500, channelIds = channelIds)
        resp.message ?: resp.status ?: "Resolve started"
    }

    suspend fun upgradeQuality(channelIds: String? = null): Result<String> = io {
        val resp = api.upgradeQuality(channelIds = channelIds, chunkSize = 25)
        resp.message ?: resp.status ?: "Upgrade started"
    }

    private suspend fun <T> io(block: suspend () -> T): Result<T> =
        withContext(Dispatchers.IO) {
            try { Result.success(block()) } catch (e: Exception) { Result.failure(e) }
        }
}

fun VideoDto.displayTitle(): String = title?.takeIf { it.isNotBlank() } ?: "Untitled"

fun VideoDto.providerLabel(): String = when (sourceProvider?.lowercase()) {
    "youtube", "ytdlp_playlist"  -> "YouTube"
    "vimeo", "vimeo_channel"     -> "Vimeo"
    "reddit", "reddit_subreddit" -> "Reddit"
    null                         -> "Source"
    else                         -> sourceProvider.replaceFirstChar { it.uppercase() }
}
