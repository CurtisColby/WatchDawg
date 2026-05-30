package com.watchdawg.tv

import android.app.Application
import android.content.Context
import com.watchdawg.tv.data.api.ApiClient
import com.watchdawg.tv.data.prefs.DefaultChannelPrefs
import com.watchdawg.tv.data.prefs.ResumeState
import com.watchdawg.tv.data.prefs.ServerPrefs
import com.watchdawg.tv.data.repo.WatchDawgRepository
import com.watchdawg.tv.playback.PlayerManager
import com.watchdawg.tv.playback.StreamUrlResolver

class WatchDawgApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Graph.init(this)
    }
}

object Graph {
    lateinit var serverPrefs: ServerPrefs
        private set
    lateinit var defaultChannelPrefs: DefaultChannelPrefs
        private set
    lateinit var resumeState: ResumeState
        private set
    lateinit var apiClient: ApiClient
        private set
    lateinit var repository: WatchDawgRepository
        private set
    lateinit var streamResolver: StreamUrlResolver
        private set

    // Singleton PlayerManager — lives at app scope so the ExoPlayer instance
    // survives navigation back from PlayerScreen to the feed, enabling the
    // inline mini-player to keep playing while the user browses.
    // Released only in MainActivity.onDestroy().
    private var _playerManager: PlayerManager? = null

    fun playerManager(context: Context): PlayerManager {
        if (_playerManager == null) {
            _playerManager = PlayerManager(
                context = context.applicationContext,
                okHttpClient = apiClient.okHttpClient(),
                streamResolver = streamResolver,
            )
        }
        return _playerManager!!
    }

    /**
     * Returns the PlayerManager if it has already been created, or null if it
     * hasn't been initialised yet. Used in MainActivity lifecycle callbacks
     * (onStop, onResume) where the player may not exist yet — calling the
     * standard playerManager(context) would create one unnecessarily.
     */
    fun playerManagerIfExists(): PlayerManager? = _playerManager

    fun releasePlayerManager() {
        _playerManager?.release()
        _playerManager = null
    }

    fun init(app: WatchDawgApp) {
        serverPrefs          = ServerPrefs(app)
        defaultChannelPrefs  = DefaultChannelPrefs(app)
        resumeState          = ResumeState(app)
        apiClient            = ApiClient(serverPrefs)
        repository           = WatchDawgRepository(apiClient)
        streamResolver       = StreamUrlResolver(baseUrlProvider = { serverPrefs.getBaseUrl() })
    }
}
