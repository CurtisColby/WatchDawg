package com.watchdawg.tv.data.api

import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.prefs.ServerPrefs
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.moshi.MoshiConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Builds and holds the Retrofit-backed [WatchDawgApi].
 *
 * Two behaviors worth calling out:
 *
 * 1. DYNAMIC BASE URL. The user can change the server address in Settings at
 *    runtime. Rather than rebuild Retrofit on every change, a request
 *    interceptor rewrites the host/port/scheme of each outgoing request to the
 *    current value from ServerPrefs. Retrofit is created once with a throwaway
 *    placeholder base URL.
 *
 * 2. AUTOMATIC TOKEN INJECTION. If a PIN session token is held in memory
 *    (TokenHolder), it is attached as X-WatchDawg-Token on every request. When
 *    locked, no header is sent and the backend hides locked channels. The token
 *    is never read from or written to disk here.
 */
class ApiClient(private val serverPrefs: ServerPrefs) {

    private val moshi: Moshi = Moshi.Builder()
        .add(KotlinJsonAdapterFactory())
        .build()

    private val baseUrlInterceptor = okhttp3.Interceptor { chain ->
        val current = serverPrefs.getBaseUrl().toHttpUrlOrNull()
        var request = chain.request()
        if (current != null) {
            // Only rewrite requests that are already targeting the WatchDawg
            // backend host. This prevents ExoPlayer from having its Plex stream
            // URLs (port 32400) rewritten to the WatchDawg port (6868).
            // ExoPlayer reuses this same OkHttpClient, so without this guard
            // every external stream URL would be redirected to the backend.
            val requestHost = request.url.host
            val requestPort = request.url.port
            val backendHost = current.host
            val backendPort = current.port
            val isWatchDawgRequest = requestHost == backendHost && requestPort == backendPort
            val isPlaceholder = requestHost == "placeholder.invalid"
            if (isWatchDawgRequest || isPlaceholder) {
                val newUrl = request.url.newBuilder()
                    .scheme(current.scheme)
                    .host(current.host)
                    .port(current.port)
                    .build()
                request = request.newBuilder().url(newUrl).build()
            }
            // External URLs (Plex, CDN, etc.) pass through unchanged
        }
        chain.proceed(request)
    }

    private val tokenInterceptor = okhttp3.Interceptor { chain ->
        val token = TokenHolder.token
        val request = if (!token.isNullOrEmpty()) {
            chain.request().newBuilder()
                .header("X-WatchDawg-Token", token)
                .build()
        } else {
            chain.request()
        }
        chain.proceed(request)
    }

    private val logging = HttpLoggingInterceptor().apply {
        level = HttpLoggingInterceptor.Level.BASIC
    }

    private val okHttp: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(baseUrlInterceptor)
        .addInterceptor(tokenInterceptor)
        .addInterceptor(logging)
        // yt-dlp resolution can take a while server-side; be patient.
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    val api: WatchDawgApi = Retrofit.Builder()
        // Placeholder; the interceptor rewrites host/port on every call.
        .baseUrl("http://placeholder.invalid/")
        .client(okHttp)
        .addConverterFactory(MoshiConverterFactory.create(moshi))
        .build()
        .create(WatchDawgApi::class.java)

    /** Shared OkHttp instance so Media3 reuses the same client/interceptors. */
    fun okHttpClient(): OkHttpClient = okHttp
}
