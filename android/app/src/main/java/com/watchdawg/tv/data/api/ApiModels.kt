package com.watchdawg.tv.data.api

import com.squareup.moshi.Json
import com.squareup.moshi.JsonClass

@JsonClass(generateAdapter = true)
data class FeedResponse(
    val total: Int = 0,
    val offset: Int = 0,
    val limit: Int = 0,
    @Json(name = "locked_channels_hidden") val lockedChannelsHidden: Boolean = false,
    val videos: List<VideoDto> = emptyList(),
)

@JsonClass(generateAdapter = true)
data class FeedIdsResponse(
    val ids: List<FeedIdItem> = emptyList(),
    val total: Int = 0,
)

@JsonClass(generateAdapter = true)
data class FeedIdItem(
    val id: Int,
    val title: String? = null,
)

@JsonClass(generateAdapter = true)
data class VideoDto(
    val id: Int,
    @Json(name = "source_provider") val sourceProvider: String? = null,
    @Json(name = "source_post_id") val sourcePostId: String? = null,
    @Json(name = "source_url") val sourceUrl: String? = null,
    @Json(name = "channel_id") val channelId: Int? = null,
    val title: String? = null,
    val artist: String? = null,
    @Json(name = "thumbnail_url") val thumbnailUrl: String? = null,
    @Json(name = "reddit_score") val redditScore: Long? = null,
    @Json(name = "resolution_status") val resolutionStatus: String? = null,
    @Json(name = "resolution_error") val resolutionError: String? = null,
    @Json(name = "resolved_stream_url") val resolvedStreamUrl: String? = null,
    @Json(name = "created_at") val createdAt: String? = null,
    @Json(name = "tmdb_poster_url") val tmdbPosterUrl: String? = null,
    @Json(name = "tmdb_year") val tmdbYear: Int? = null,
    @Json(name = "tmdb_rating") val tmdbRating: Float? = null,
)

@JsonClass(generateAdapter = true)
data class ResolveResponse(
    val id: Int,
    val title: String? = null,
    val artist: String? = null,
    @Json(name = "stream_url") val streamUrl: String? = null,
    @Json(name = "audio_url") val audioUrl: String? = null,
    val format: String? = null,
    @Json(name = "resolved_at") val resolvedAt: String? = null,
    @Json(name = "source_url") val sourceUrl: String? = null,
    @Json(name = "thumbnail_url") val thumbnailUrl: String? = null,
)

@JsonClass(generateAdapter = true)
data class ChannelListResponse(
    val channels: List<ChannelDto> = emptyList(),
)

@JsonClass(generateAdapter = true)
data class ChannelDto(
    val id: Int,
    val name: String,
    @Json(name = "channel_type") val channelType: String? = null,
    val enabled: Boolean = true,
    val locked: Boolean = false,
    val category: String? = null,
    @Json(name = "genre_tags") val genreTags: String? = null,
    @Json(name = "video_count") val videoCount: Int = 0,
)

@JsonClass(generateAdapter = true)
data class SkipRequest(
    @Json(name = "video_id") val videoId: Int,
)

@JsonClass(generateAdapter = true)
data class UnlockResponse(
    val status: String? = null,
    val token: String? = null,
)

@JsonClass(generateAdapter = true)
data class AuthStatusResponse(
    @Json(name = "pin_lock_enabled") val pinLockEnabled: Boolean = false,
    @Json(name = "is_unlocked") val isUnlocked: Boolean = false,
)

@JsonClass(generateAdapter = true)
data class FavoriteListResponse(
    val favorites: List<FavoriteDto> = emptyList(),
)

@JsonClass(generateAdapter = true)
data class FavoriteDto(
    val id: Int? = null,
    @Json(name = "video_id") val videoId: Int? = null,
    val title: String? = null,
    val artist: String? = null,
    @Json(name = "source_provider") val sourceProvider: String? = null,
    @Json(name = "thumbnail_url") val thumbnailUrl: String? = null,
    @Json(name = "channel_name") val channelName: String? = null,
    @Json(name = "channel_locked") val channelLocked: Boolean = false,
    @Json(name = "download_status") val downloadStatus: String? = null,
    @Json(name = "download_error") val downloadError: String? = null,
    @Json(name = "stream_url") val streamUrl: String? = null,
)

@JsonClass(generateAdapter = true)
data class LibraryResponse(
    val total: Int = 0,
    val directory: String? = null,
    val files: List<LibraryFileDto> = emptyList(),
    @Json(name = "locked_hidden") val lockedHidden: Boolean = false,
)

@JsonClass(generateAdapter = true)
data class LibraryFileDto(
    val filename: String? = null,
    @Json(name = "relative_path") val relativePath: String? = null,
    val subfolder: String? = null,
    val title: String? = null,
    val artist: String? = null,
    @Json(name = "thumbnail_url") val thumbnailUrl: String? = null,
    @Json(name = "size_bytes") val sizeBytes: Long? = null,
    @Json(name = "size_human") val sizeHuman: String? = null,
    @Json(name = "stream_url") val streamUrl: String? = null,
    @Json(name = "video_id") val videoId: Int? = null,
    // Session 42: genre tags and channel name for pill filtering in Local tab
    @Json(name = "genre_tags") val genreTags: String? = null,
    @Json(name = "channel_name") val channelName: String? = null,
)

@JsonClass(generateAdapter = true)
data class StatusResponse(
    val status: String? = null,
    val message: String? = null,
)

@JsonClass(generateAdapter = true)
data class WatchlistResponse(
    val watchlist: List<WatchlistItemDto> = emptyList(),
    val total: Int = 0,
)

@JsonClass(generateAdapter = true)
data class WatchlistItemDto(
    val id: Int,
    @Json(name = "video_id") val videoId: Int,
    val title: String? = null,
    val artist: String? = null,
    @Json(name = "thumbnail_url") val thumbnailUrl: String? = null,
    @Json(name = "source_provider") val sourceProvider: String? = null,
    @Json(name = "source_url") val sourceUrl: String? = null,
    @Json(name = "channel_id") val channelId: Int? = null,
    @Json(name = "channel_name") val channelName: String? = null,
    @Json(name = "duration_seconds") val durationSeconds: Float? = null,
    @Json(name = "resolution_status") val resolutionStatus: String? = null,
    @Json(name = "tmdb_poster_url") val tmdbPosterUrl: String? = null,
    @Json(name = "tmdb_year") val tmdbYear: Int? = null,
    @Json(name = "added_at") val addedAt: String? = null,
)

@JsonClass(generateAdapter = true)
data class HistoryResponse(
    val history: List<HistoryItemDto> = emptyList(),
    val total: Int = 0,
)

@JsonClass(generateAdapter = true)
data class HistoryItemDto(
    @Json(name = "video_id") val videoId: Int,
    val title: String? = null,
    val artist: String? = null,
    @Json(name = "thumbnail_url") val thumbnailUrl: String? = null,
    @Json(name = "source_provider") val sourceProvider: String? = null,
    @Json(name = "channel_id") val channelId: Int? = null,
    @Json(name = "channel_name") val channelName: String? = null,
    @Json(name = "duration_seconds") val durationSeconds: Float? = null,
    @Json(name = "position_seconds") val positionSeconds: Float? = null,
    @Json(name = "progress_pct") val progressPct: Float? = null,
    val completed: Boolean = false,
    @Json(name = "last_watched_at") val lastWatchedAt: String? = null,
    @Json(name = "tmdb_poster_url") val tmdbPosterUrl: String? = null,
    @Json(name = "tmdb_year") val tmdbYear: Int? = null,
)

@JsonClass(generateAdapter = true)
data class HistoryUpdateRequest(
    @Json(name = "position_seconds") val positionSeconds: Float,
    @Json(name = "duration_seconds") val durationSeconds: Float? = null,
)

@JsonClass(generateAdapter = true)
data class SeriesResponse(
    val series: List<SeriesItemDto> = emptyList(),
    val total: Int = 0,
    @Json(name = "locked_channels_hidden") val lockedChannelsHidden: Boolean = false,
)

@JsonClass(generateAdapter = true)
data class SeriesItemDto(
    @Json(name = "channel_id") val channelId: Int,
    @Json(name = "channel_name") val channelName: String,
    @Json(name = "genre_tags") val genreTags: String? = null,
    @Json(name = "episode_count") val episodeCount: Int = 0,
    @Json(name = "latest_thumbnail") val latestThumbnail: String? = null,
    @Json(name = "tmdb_poster_url") val tmdbPosterUrl: String? = null,
    @Json(name = "tmdb_description") val tmdbDescription: String? = null,
    @Json(name = "tmdb_year") val tmdbYear: Int? = null,
    @Json(name = "tmdb_rating") val tmdbRating: Float? = null,
)

@JsonClass(generateAdapter = true)
data class EpisodesResponse(
    @Json(name = "channel_id") val channelId: Int,
    @Json(name = "channel_name") val channelName: String,
    val total: Int = 0,
    val episodes: List<VideoDto> = emptyList(),
)

@JsonClass(generateAdapter = true)
data class GenresResponse(
    val category: String,
    val tags: List<String> = emptyList(),
)

// ── Live TV (Milestone I / Session 36) ───────────────────────────────────────

/**
 * One live TV channel from GET /live-tv/channels.
 *
 * Session 36 additions:
 *   [isFavorite]  — user-starred; true = show in Favorites row at top of LiveTvScreen.
 *   [sortOrder]   — group display order set via backend Group Order panel.
 */
@JsonClass(generateAdapter = true)
data class LiveTvChannelDto(
    val id: Int,
    val name: String,
    @Json(name = "logo_url")     val logoUrl: String? = null,
    @Json(name = "stream_url")   val streamUrl: String? = null,
    @Json(name = "group_name")   val groupName: String? = null,
    @Json(name = "channel_type") val channelType: String? = "real",
    @Json(name = "is_online")    val isOnline: Boolean? = null,
    @Json(name = "is_favorite")  val isFavorite: Boolean = false,
    @Json(name = "sort_order")   val sortOrder: Int = 999,
    @Json(name = "last_checked") val lastChecked: String? = null,
    @Json(name = "source_m3u")  val sourceM3u: String? = null,
    @Json(name = "created_at")   val createdAt: String? = null,
)

@JsonClass(generateAdapter = true)
data class LiveTvResponse(
    val channels: List<LiveTvChannelDto> = emptyList(),
    val total: Int = 0,
)

/** DELETE /live-tv/channels/{id} response. */
@JsonClass(generateAdapter = true)
data class DeleteLiveTvChannelResponse(
    val status: String? = null,
    val name: String? = null,
)

/** POST /live-tv/channels/{id}/favorite response. */
@JsonClass(generateAdapter = true)
data class ToggleLiveTvFavoriteResponse(
    val status: String? = null,
    val id: Int? = null,
    @Json(name = "is_favorite") val isFavorite: Boolean = false,
)

// End of ApiModels
