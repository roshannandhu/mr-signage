package com.olrac.signage.sync

import android.content.Context
import android.net.Uri
import com.olrac.signage.BuildConfig
import com.olrac.signage.data.AppDatabase
import com.olrac.signage.data.DeviceState
import com.olrac.signage.data.PlaylistItemEntity
import com.olrac.signage.network.ApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import java.util.concurrent.TimeUnit

data class SyncOutcome(
    val successful: Boolean,
    val retryable: Boolean,
    val changed: Boolean,
    val intervalSeconds: Int,
    val error: String? = null
)

class PlaylistSynchronizer(context: Context) {
    private val appContext = context.applicationContext
    private val preferences = appContext.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
    private val api by lazy { ApiClient.service(appContext) }
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.MINUTES)
        .callTimeout(10, TimeUnit.MINUTES)
        .addNetworkInterceptor { chain ->
            val resp = chain.proceed(chain.request())
            resp.header("Date")?.let {
                com.olrac.signage.data.SignageClock.getInstance(appContext).updateFromHttpDate(it)
            }
            resp
        }
        .build()
    private val storageManager = StorageManager(appContext)

    suspend fun sync(): SyncOutcome = GLOBAL_SYNC_MUTEX.withLock {
        withContext(Dispatchers.IO) {
            val currentInterval = SyncBackoffPolicy.serverIntervalSeconds(
                preferences.getInt(KEY_SYNC_INTERVAL_SECONDS, 60)
            )
            // Via DeviceState, which derives and persists the id on first access. Reading
            // the preference raw returned null on a fresh install whenever the service
            // started before MainActivity ever ran -- which is exactly what BootReceiver
            // does -- and this reported "Device is not registered" with retryable = false,
            // so sync stayed dead until someone opened the app by hand.
            val deviceId = DeviceState(appContext).deviceId

            cleanupStaleDownloads()
            try {
                val dao = AppDatabase.getDatabase(appContext).playlistDao()
                // An empty local playlist must always request a full snapshot. This
                // prevents a matching version marker from trapping a repaired or
                // newly provisioned database in an empty state.
                //
                // Also full after an update attempt went wrong. The update offer only
                // travels in a full sync body, so a screen that downloaded half an APK and
                // lost its connection -- the ordinary case for one that has just come back
                // online -- was then told 204 on every later sync and never offered that
                // release again until something unrelated changed its playlist.
                UpdateManager.clearStaleGuards(appContext)
                val since = preferences.getString(KEY_PLAYLIST_UPDATED_AT, null)
                    .takeIf { dao.hasItems() }
                    .takeUnless { UpdateManager.consumeFullSyncRequest(appContext) }
                val response = api.sync(
                    deviceId = deviceId,
                    since = since
                )
                val responseInterval = response.headers()[SYNC_INTERVAL_HEADER]?.toIntOrNull()

                if (response.code() == 204) {
                    val interval = saveInterval(responseInterval ?: currentInterval)
                    return@withContext SyncOutcome(true, false, false, interval)
                }
                // 404, and ONLY 404, means this screen is gone. It is not a transient failure
                // and must not be retried like one: the server has no screen for this device
                // id, so the operator removed this TV from their fleet. Treated as retryable
                // the panel keeps playing the removed tenant's cached playlist forever,
                // reconnecting every minute to a workspace it no longer belongs to.
                if (response.code() == 404) {
                    android.util.Log.w("PlaylistSynchronizer", "Screen removed from its workspace (404); signing out")
                    com.olrac.signage.boot.PlayerLauncher.handleUnpairedOrDeleted(appContext)
                    return@withContext SyncOutcome(
                        successful = false,
                        retryable = false,
                        changed = true,
                        intervalSeconds = currentInterval,
                        error = "This screen was removed from its workspace"
                    )
                }
                // 401/403 is NOT "you were deleted". The device secret is still valid; what
                // lapsed is the token minted from it, and ApiClient has already dropped the
                // cached one -- so the next call re-mints and succeeds on its own.
                //
                // These used to be handled as deletion, which wiped the pairing, the device
                // secret, the cached playlist AND every downloaded file over a momentary auth
                // failure. The worst case is routine rather than exotic: rotating the server's
                // SECRET_KEY invalidates every device token at once, so the whole fleet would
                // factory-reset itself and need re-pairing by hand. Retry instead. A screen
                // whose access was genuinely revoked comes back as a 404 and is handled above.
                if (response.code() == 401 || response.code() == 403) {
                    android.util.Log.w(
                        "PlaylistSynchronizer",
                        "Auth rejected (HTTP ${response.code()}); re-authenticating on the next sync"
                    )
                    ApiClient.clearToken()
                    return@withContext SyncOutcome(
                        successful = false,
                        retryable = true,
                        changed = false,
                        intervalSeconds = currentInterval,
                        error = "Authentication failed; retrying"
                    )
                }
                if (!response.isSuccessful) {
                    return@withContext SyncOutcome(
                        successful = false,
                        retryable = true,
                        changed = false,
                        intervalSeconds = currentInterval,
                        error = "Sync failed with HTTP ${response.code()}"
                    )
                }

                val syncData = response.body() ?: return@withContext SyncOutcome(
                    successful = false,
                    retryable = true,
                    changed = false,
                    intervalSeconds = currentInterval,
                    error = "Sync response was empty"
                )
                val interval = saveInterval(syncData.sync_interval_seconds ?: responseInterval)

                if (syncData.screen_id != null || syncData.organization_id != null) {
                    preferences.edit().apply {
                        syncData.screen_id?.let { putInt("screen_id", it) }
                        syncData.organization_id?.let { putInt("organization_id", it) }
                        apply()
                    }
                }

                // Written every sync, cleared as soon as it comes back null, so the notice
                // disappears on its own the moment the workspace is paid up again -- no
                // restart, no re-pair. Stored rather than passed through because the player
                // reads its playlist from the local database, not from this response.
                preferences.edit().putString(KEY_SERVICE_STATE, syncData.service_state).apply()

                // Same reasoning as service_state above: stored so the waiting/idle screen
                // (PlayerScreen, which reads its playlist from the database, not this
                // response) can show it, and so it updates on its own if the tenant changes
                // their brand without the TV being restarted.
                preferences.edit()
                    .putString(KEY_BRAND_NAME, syncData.brand_name)
                    .putString(KEY_BRAND_LOGO_URL, syncData.brand_logo_url)
                    .putString(KEY_BRAND_COLOR, syncData.brand_color)
                    // Missing on an old server means stay locked -- see the field's own
                    // comment on SyncResponseDto for why this is resolved here, not left to
                    // a Kotlin default.
                    .putBoolean(KEY_KIOSK_LOCK, syncData.kiosk_lock ?: true)
                    .apply()

                // pending_commands carries every command still within its hold time, for a
                // screen that queued more than one while offline; pending_command is the
                // first of those, kept so this still works against an older server that has
                // not been upgraded. Deduplicated because both name the same thing there.
                val commands = PendingCommands.merge(syncData.pending_command, syncData.pending_commands)
                for (cmd in commands) {
                    if (cmd == "deregister") {
                        com.olrac.signage.boot.PlayerLauncher.handleUnpairedOrDeleted(appContext)
                        return@withContext SyncOutcome(
                            successful = true,
                            retryable = false,
                            changed = true,
                            intervalSeconds = interval,
                        )
                    }
                    if (cmd == "check_update") {
                        // Processed before app_version below, so the offer in this same
                        // body is acted on immediately rather than waiting out a backoff.
                        UpdateManager.requestUpdateCheck(appContext)
                    }
                    if (cmd == "request_screenshot") {
                        com.olrac.signage.telemetry.ScreenshotManager.requestScreenshot()
                    }
                    if (cmd == "remote_unlock") {
                        // Flag consumed by MainActivity's own poll, which opens maintenance
                        // as if the PIN had just been entered -- this call only makes sure
                        // the TV is actually in front to show it.
                        appContext.getSharedPreferences("signage_prefs", android.content.Context.MODE_PRIVATE)
                            .edit().putBoolean(com.olrac.signage.MainActivity.PREF_PENDING_REMOTE_UNLOCK, true).apply()
                        android.util.Log.i("PlaylistSynchronizer", "Received remote_unlock command from sync")
                        com.olrac.signage.boot.PlayerLauncher.launch(appContext, delayMs = 500L, reason = "remote_unlock")
                    }
                    if ((cmd == "bring_to_front" || cmd == "launch_app") && com.olrac.signage.boot.RemoteLaunchGate.allow()) {
                        android.util.Log.i("PlaylistSynchronizer", "Received $cmd command from sync; bringing app to front")
                        com.olrac.signage.boot.PlayerLauncher.launch(appContext, delayMs = 500L, reason = "sync_command")
                    } else if (cmd == "reset" || cmd == "unpair") {
                        android.util.Log.w("PlaylistSynchronizer", "Received $cmd command from sync; signing out")
                        com.olrac.signage.boot.PlayerLauncher.handleUnpairedOrDeleted(appContext)
                        return@withContext SyncOutcome(false, false, true, interval, "Screen reset command")
                    }
                }

                syncData.app_version?.let { version ->
                    if (version.version_code > BuildConfig.VERSION_CODE && !version.apk_url.isNullOrBlank()) {
                        val inFlightKey = "$KEY_UPDATE_IN_FLIGHT${version.version_code}"
                        if (!preferences.getBoolean(inFlightKey, false) &&
                            UpdateManager.retryDue(appContext, version.version_code)
                        ) {
                            preferences.edit().putBoolean(inFlightKey, true).apply()

                            // Download off the sync path so playback and polling continue.
                            @Suppress("OPT_IN_USAGE")
                            GlobalScope.launch(Dispatchers.IO) {
                                val installed = UpdateManager.downloadAndInstallUpdate(
                                    appContext, version, client
                                )
                                if (!installed) {
                                    // Release the guard AND ask for the offer again: one
                                    // dropped download must not block this version for
                                    // ever. On success the flag stays set -- the install
                                    // replaces the process, and a higher version_code uses
                                    // a different key anyway.
                                    UpdateManager.scheduleRetry(appContext, version.version_code)
                                }
                            }
                        }
                    }
                }

                // Cached ahead of the empty-playlist return: a screen with nothing scheduled
                // still has to be serviceable from the remote.
                // Written every sync including when absent, so clearing the master code in
                // the console actually takes it off the panels rather than leaving a key that
                // works for ever on whichever screen happened to cache it.
                DeviceState(appContext).setMasterPin(syncData.master_pin)

                syncData.maintenance_pin?.takeIf { it.isNotBlank() }?.let {
                    DeviceState(appContext).setMaintenancePin(it)
                }

                // Persisted for the same reason as the pin, and ahead of the same early
                // return: the screen must keep observing its opening hours through an
                // outage, so the player reads them from disk rather than from the last
                // successful response.
                DeviceState(appContext).setOperatingHours(
                    syncData.operating_mode,
                    syncData.operating_hours,
                )

                val playlist = syncData.playlist
                if (playlist == null) {
                    dao.replaceAll(emptyList())
                    cleanupOldCache(emptySet())
                    preferences.edit()
                        .putString(KEY_PLAYLIST_UPDATED_AT, syncData.playlist_updated_at)
                        .apply()
                    return@withContext SyncOutcome(true, false, true, interval)
                }

                val targets = playlist.items.map { item ->
                    val fileName = cacheFileName(item.content.id, item.content.file_url)
                    val finalFile = File(appContext.filesDir, fileName)
                    val entity = PlaylistItemEntity(
                        id = item.id,
                        contentId = item.content.id,
                        playlistId = playlist.id,
                        type = item.content.type,
                        fileUrl = item.content.file_url,
                        localPath = null,
                        duration = item.duration,
                        orderIndex = item.order,
                        startAt = item.start_at,
                        endAt = item.end_at,
                        daysOfWeek = item.schedule?.days_of_week?.joinToString(","),
                        windowStart = item.schedule?.start_time,
                        windowEnd = item.schedule?.end_time,
                        transition = item.transition,
                        transitionMs = item.transition_ms,
                        rotation = item.rotation ?: 0,
                        fitMode = syncData.fit_mode ?: "contain",
                        playlistDefaultTransition = playlist.default_transition ?: "fade",
                        playlistDefaultTransitionMs = playlist.default_transition_ms ?: 600,
                        sha256 = item.content.sha256,
                        fileSizeBytes = item.content.file_size_bytes
                    )
                    ActivationTarget(entity, finalFile)
                }

                // Files the player may be reading right now, plus everything this sync
                // intends to activate. Eviction must never touch these, or a playlist
                // switch under storage pressure deletes the ad currently on screen.
                val existingItems = dao.getAllItems()
                val protectedNames = buildSet {
                    existingItems.forEach { existing ->
                        existing.localPath?.let { add(File(it).name) }
                    }
                    targets.forEach { add(it.finalFile.name) }
                }
                // What we last verified on disk for each content id, so a changed digest
                // can be told from an unchanged one without re-hashing whole videos on
                // every sync. See MediaCacheFreshness.
                val recordedSha = existingItems.associate { it.contentId to it.sha256 }

                val staged = mutableListOf<StagedDownload>()
                val readyTargets = mutableListOf<ActivationTarget>()
                for (target in targets) {
                    val cachedIsCurrent = MediaCacheFreshness.canReuseCachedFile(
                        recordedSha256 = recordedSha[target.entity.contentId],
                        advertisedSha256 = target.entity.sha256
                    )
                    val fileAlreadyValid = target.finalFile.isFile && target.finalFile.length() > 0L && (
                        cachedIsCurrent || storageManager.verifyFileIntegrity(
                            target.finalFile,
                            target.entity.sha256,
                            target.entity.fileSizeBytes
                        )
                    )
                    if (fileAlreadyValid) {
                        readyTargets.add(target)
                    } else {
                        if (!cachedIsCurrent && target.finalFile.isFile) {
                            android.util.Log.i(
                                "PlaylistSynchronizer",
                                "Content ${target.entity.contentId} changed on the server; re-fetching"
                            )
                        }
                        val tempFile = storageManager.downloadWithIntegrityCheck(
                            client = client,
                            url = ApiClient.resolveMediaUrl(appContext, target.entity.fileUrl),
                            finalFile = target.finalFile,
                            expectedSha256 = target.entity.sha256,
                            expectedSizeBytes = target.entity.fileSizeBytes,
                            protectedNames = protectedNames
                        )
                        if (tempFile != null) {
                            staged += StagedDownload(tempFile, target.finalFile)
                            readyTargets.add(target)
                        } else {
                            android.util.Log.w("PlaylistSynchronizer", "Item ${target.entity.id} (${target.entity.fileUrl}) failed download; continuing with available items")
                        }
                    }
                }

                staged.forEach(::promoteStagedFile)
                val activatedItems = readyTargets.map { target ->
                    target.entity.copy(localPath = target.finalFile.absolutePath)
                }

                // Assigned, but could not be fetched. Recorded so the player can say THAT,
                // instead of "Waiting for assigned content" -- which blames the operator for
                // a server problem and is exactly the message that sent a real media outage
                // chasing bookings that were fine all along.
                //
                // Counts the ones that actually failed rather than only the total-wipeout
                // case: a screen playing two old adverts and missing the new one was
                // reporting zero unreachable, so the one symptom anybody could see was "the
                // advert I just assigned is not playing" with nothing anywhere saying why.
                val unreachable = targets.size - activatedItems.size
                preferences.edit().putInt(KEY_UNREACHABLE_ITEMS, unreachable).apply()

                // If we have ready items, or if the server genuinely sent an empty playlist,
                // activate them so playback can begin.
                if (activatedItems.isNotEmpty() || targets.isEmpty()) {
                    dao.replaceAll(activatedItems)
                    cleanupOldCache(readyTargets.mapTo(mutableSetOf()) { it.finalFile.name })

                    // Only claim to be caught up when every assigned item is actually on
                    // disk. This marker is what the next sync sends as `since`, and the
                    // server answers 204 to anything it has already covered -- so writing
                    // it after a PARTIAL download told the screen it was up to date while
                    // an advert it had never managed to fetch was quietly dropped from the
                    // loop. One failed download (an expired media URL, a blip mid-transfer)
                    // stranded that advert permanently: it was never in the playlist, never
                    // re-offered, and the booking read "Running" on the dashboard for its
                    // whole paid window while the screen played everything except it.
                    //
                    // Holding the old marker back costs one unchanged playlist per minute
                    // until the fetch succeeds, and then it settles to 204 on its own.
                    if (unreachable == 0) {
                        preferences.edit()
                            .putString(KEY_PLAYLIST_UPDATED_AT, syncData.playlist_updated_at)
                            .apply()
                    } else {
                        android.util.Log.w(
                            "PlaylistSynchronizer",
                            "$unreachable of ${targets.size} assigned items could not be " +
                                "fetched; holding the sync marker so they are retried"
                        )
                    }
                }
                SyncOutcome(true, false, true, interval)
            } catch (exception: Exception) {
                SyncOutcome(
                    successful = false,
                    retryable = true,
                    changed = false,
                    intervalSeconds = currentInterval,
                    error = exception.message ?: exception.javaClass.simpleName
                )
            }
        }
    }

    private fun saveInterval(value: Int?): Int {
        val interval = SyncBackoffPolicy.serverIntervalSeconds(value)
        preferences.edit().putInt(KEY_SYNC_INTERVAL_SECONDS, interval).apply()
        return interval
    }

    private fun cacheFileName(contentId: Int, url: String): String {
        val segment = Uri.parse(url).lastPathSegment ?: "media"
        val extension = segment.substringAfterLast('.', "bin").take(8)
        return "content-$contentId.$extension"
    }

    // `downloadToStaging` removed, logic is in StorageManager

    private fun promoteStagedFile(download: StagedDownload) {
        try {
            Files.move(
                download.temporaryFile.toPath(),
                download.finalFile.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING
            )
        } catch (_: AtomicMoveNotSupportedException) {
            Files.move(
                download.temporaryFile.toPath(),
                download.finalFile.toPath(),
                StandardCopyOption.REPLACE_EXISTING
            )
        }
    }

    private fun cleanupOldCache(validFileNames: Set<String>) {
        appContext.filesDir.listFiles()?.forEach { file ->
            if (file.name.startsWith("content-") && file.name !in validFileNames) {
                file.delete()
            }
        }
    }

    private fun cleanupStaleDownloads() {
        val yesterday = System.currentTimeMillis() - 24 * 60 * 60 * 1000L
        appContext.filesDir.listFiles()?.forEach { file ->
            if (file.name.startsWith(StorageManager.PART_PREFIX) && file.lastModified() < yesterday) {
                file.delete()
            }
        }
    }

    private data class ActivationTarget(
        val entity: PlaylistItemEntity,
        val finalFile: File
    )

    private data class StagedDownload(
        val temporaryFile: File,
        val finalFile: File
    )

    companion object {
        private val GLOBAL_SYNC_MUTEX = Mutex()
        private const val PREFERENCES_NAME = "signage_prefs"
        /** How many assigned items could not be fetched on the last sync; 0 when all is well. */
        private const val KEY_UNREACHABLE_ITEMS = "unreachable_items"

        /**
         * Adverts the server has assigned to this screen that could not be downloaded.
         *
         * The player shows this instead of "Waiting for assigned content", which blamed the
         * operator for a server problem: during a media outage the TV said nothing was
         * booked while four paid campaigns sat on it.
         */
        fun unreachableItemCount(context: Context): Int =
            context.applicationContext
                .getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
                .getInt(KEY_UNREACHABLE_ITEMS, 0)

        /** Why the workspace's own adverts are not playing, or null when they are. */
        private const val KEY_SERVICE_STATE = "service_state"

        /**
         * The server's reason for withholding this workspace's adverts, or null.
         *
         * One of "pending_approval", "suspended", "rejected", "expired". The demo reel plays
         * underneath in every case; this only decides the caption over it.
         */
        fun serviceState(context: Context): String? =
            context.applicationContext
                .getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
                .getString(KEY_SERVICE_STATE, null)

        private const val KEY_BRAND_NAME = "brand_name"
        private const val KEY_BRAND_LOGO_URL = "brand_logo_url"
        private const val KEY_BRAND_COLOR = "brand_color"

        /**
         * The tenant's own brand, for the waiting/idle screen once this TV has paired.
         *
         * Null fields fall back to "MR Signage" and plain black, same as the dashboard
         * header and every report -- the platform's own mark belongs on the sign-in screen
         * only, which never reaches this far.
         */
        data class Branding(val name: String?, val logoUrl: String?, val color: String?)

        fun branding(context: Context): Branding {
            val prefs = context.applicationContext
                .getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
            return Branding(
                name = prefs.getString(KEY_BRAND_NAME, null),
                logoUrl = prefs.getString(KEY_BRAND_LOGO_URL, null),
                color = prefs.getString(KEY_BRAND_COLOR, null),
            )
        }

        private const val KEY_KIOSK_LOCK = "kiosk_lock"

        /**
         * Whether this tenant's plan sells the TV lock feature, as of the last sync.
         *
         * Defaults true -- the safe failure before the first sync has ever completed, same
         * as a sync response missing the field entirely. A screen only ever becomes
         * unlocked after the server has explicitly said so.
         */
        fun kioskLockEnabled(context: Context): Boolean =
            context.applicationContext
                .getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
                .getBoolean(KEY_KIOSK_LOCK, true)

        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_PLAYLIST_UPDATED_AT = "playlist_updated_at"
        private const val KEY_SYNC_INTERVAL_SECONDS = "sync_interval_seconds"
        private const val KEY_PENDING_UPDATE_URL = "pending_update_url"
        private const val KEY_PENDING_UPDATE_VERSION = "pending_update_version"
        /** Prefix; the target version_code is appended. Cleared if the download fails. */
        private const val KEY_UPDATE_IN_FLIGHT = "update_in_flight_"
        private const val SYNC_INTERVAL_HEADER = "X-Sync-Interval-Seconds"
    }
}
