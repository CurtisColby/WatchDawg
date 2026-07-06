package com.watchdawg.tv.data.api

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * Retrofit interface for the WatchDawg backend.
 *
 * Session 36: POST /live-tv/channels/{id}/favorite — toggle favorite flag.
 *             DELETE /live-tv/channels/{id} — remove channel (from session 35).
 */
interface WatchDawgApi {

    @GET("feed")
    suspend fun getFeed(
        @Query("limit") limit: Int = 1000,
        @Query("offset") offset: Int = 0,
        @Query("channel_ids") channelIds: String? = null,
        @Query("provider") provider: String? = null,
        @Query("status") status: String? = null,
        @Query("category") category: String? = null,
        @Query("genre_tag") genreTag: String? = null,
    ): FeedResponse

    @GET("feed/ids")
    suspend fun getFeedIds(
        @Query("channel_ids") channelIds: String? = null,
        @Query("category") category: String? = null,
        @Query("genre_tag") genreTag: String? = null,
        @Query("order_by") orderBy: String? = null,
    ): FeedIdsResponse

    @GET("feed/series")
    suspend fun getSeries(
        @Query("genre_tag") genreTag: String? = null,
    ): SeriesResponse

    @GET("feed/episodes")
    suspend fun getEpisodes(
        @Query("channel_id") channelId: Int,
    ): EpisodesResponse

    @GET("feed/genres")
    suspend fun getGenres(
        @Query("category") category: String,
    ): GenresResponse

    @GET("resolve/{id}")
    suspend fun resolve(
        @Path("id") id: Int,
        @Query("force") force: Boolean = false,
        @Query("client") client: String = "tv",
    ): ResolveResponse

    @GET("channel")
    suspend fun getChannels(): ChannelListResponse

    @POST("skip")
    suspend fun skip(@Body body: SkipRequest): StatusResponse

    @POST("favorite/{id}/bookmark")
    suspend fun bookmark(@Path("id") id: Int): StatusResponse

    @POST("favorite/{id}")
    suspend fun favorite(@Path("id") id: Int): StatusResponse

    @GET("favorite")
    suspend fun getFavorites(): FavoriteListResponse

    @DELETE("favorite/{id}")
    suspend fun removeFavorite(@Path("id") id: Int): StatusResponse

    @POST("favorite/{id}/retry")
    suspend fun retryFavorite(@Path("id") id: Int): StatusResponse

    @GET("library")
    suspend fun getLibrary(
        @retrofit2.http.Query("genre") genre: String? = null,
    ): LibraryResponse

    @GET("library/genres")
    suspend fun getLibraryGenres(): GenresResponse

    @DELETE("library/file")
    suspend fun deleteLibraryFile(
        @Query("relative_path") relativePath: String,
    ): StatusResponse

    @POST("library/generate-thumbnails")
    suspend fun generateLocalThumbnails(
        @Query("limit") limit: Int = 50,
    ): StatusResponse

    @POST("auth/unlock")
    suspend fun unlock(@Query("pin") pin: String): UnlockResponse

    @POST("auth/lock")
    suspend fun lock(): StatusResponse

    @GET("auth/status")
    suspend fun authStatus(): AuthStatusResponse

    @GET("watchlist")
    suspend fun getWatchlist(): WatchlistResponse

    @POST("watchlist/{id}")
    suspend fun addToWatchlist(@Path("id") videoId: Int): StatusResponse

    @DELETE("watchlist/{id}")
    suspend fun removeFromWatchlist(@Path("id") videoId: Int): StatusResponse

    @GET("history")
    suspend fun getHistory(@Query("limit") limit: Int = 50): HistoryResponse

    @DELETE("history/{id}")
    suspend fun deleteHistory(@Path("id") videoId: Int): StatusResponse

    @POST("history/{id}")
    suspend fun postHistory(
        @Path("id") videoId: Int,
        @Body body: HistoryUpdateRequest,
    ): StatusResponse

    @POST("feed/scrape")
    suspend fun scrapeAll(
        @Query("limit") limit: Int = 2000,
        @Query("channel_ids") channelIds: String? = null,
    ): StatusResponse

    @POST("resolve/batch")
    suspend fun resolveBatch(
        @Query("limit") limit: Int = 500,
        @Query("channel_ids") channelIds: String? = null,
    ): StatusResponse

    @POST("resolve/upgrade")
    suspend fun upgradeQuality(
        @Query("channel_ids") channelIds: String? = null,
        @Query("chunk_size") chunkSize: Int = 25,
    ): StatusResponse

    // ── Live TV (Milestone I / Session 36) ───────────────────────────────────

    /** GET /live-tv/channels — all live TV channels ordered by sort_order then group then name. */
    @GET("live-tv/channels")
    suspend fun getLiveChannels(): LiveTvResponse

    /** DELETE /live-tv/channels/{id} — remove a single live TV channel from the TV remote. */
    @DELETE("live-tv/channels/{id}")
    suspend fun deleteLiveChannel(
        @Path("id") id: Int,
    ): DeleteLiveTvChannelResponse

    /**
     * POST /live-tv/channels/{id}/favorite — toggle is_favorite on a channel.
     * Returns the new is_favorite state so the ViewModel can update in-memory
     * without a full reload.
     */
    @POST("live-tv/channels/{id}/favorite")
    suspend fun toggleLiveChannelFavorite(
        @Path("id") id: Int,
    ): ToggleLiveTvFavoriteResponse

}
