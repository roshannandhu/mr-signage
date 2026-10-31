package com.olrac.signage.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.UserManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.olrac.signage.MainActivity
import com.olrac.signage.R
import com.olrac.signage.boot.PlayerLauncher
import com.olrac.signage.data.DeviceState
import com.olrac.signage.network.ConnectivityWatcher
import com.olrac.signage.network.RealtimeClient
import com.olrac.signage.sync.PlaylistSynchronizer
import com.olrac.signage.sync.SyncBackoffPolicy
import com.olrac.signage.telemetry.HeartbeatReporter
import com.olrac.signage.telemetry.ScreenshotManager
import com.olrac.signage.workers.HeartbeatWorker
import com.olrac.signage.workers.SyncWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.TimeUnit

class PlaybackService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val immediateSyncSignals = Channel<Unit>(capacity = Channel.CONFLATED)
    private var pollingJob: Job? = null
    private var commandPollJob: Job? = null
    private var connectivityWatcher: ConnectivityWatcher? = null
    private var realtimeClient: RealtimeClient? = null
    private var bootFollowUp: Job? = null

    override fun onCreate() {
        super.onCreate()
        com.olrac.signage.telemetry.CrashRecorder.install(this)
        createNotificationChannel()
        promoteToForeground()
        acquireWakeLock()
        startRealtimeClient()
        // A TV "restart" from the remote is often standby, not a reboot: no BOOT_COMPLETED
        // arrives, only the screen coming back on. Treat that the same as a restart.
        ContextCompat.registerReceiver(this, screenOnReceiver, IntentFilter(Intent.ACTION_SCREEN_ON), ContextCompat.RECEIVER_NOT_EXPORTED)
        // One line per service start: whether this TV is allowed to bring the player up by
        // itself after a restart ("Display over other apps", or device owner).
        Log.i(TAG, "Reopens after restart=${PlayerLauncher.canStartFromBackground(this)}")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Boot recovery first, and never behind anything that can throw. WorkManager's store is
        // credential-encrypted, so scheduleWorkers() throws until the device is fully unlocked --
        // and this directBootAware service reaches here in the window after the user unlocks but
        // before WorkManager's startup provider has run. That crash used to take the whole
        // service down BEFORE the player was relaunched, so after a restart the TV sat on its
        // home screen. So the relaunch runs first, and the workers are guarded; USER_UNLOCKED
        // starts the service again and they schedule cleanly then.
        if (intent?.getBooleanExtra(EXTRA_LAUNCH_PLAYER, false) == true) launchPlayer()
        if (intent?.getBooleanExtra(EXTRA_AFTER_BOOT, false) == true) keepPlayerInFrontAfterStart()
        if (intent?.action == ACTION_SYNC_NOW) immediateSyncSignals.trySend(Unit)
        if (isUserUnlocked()) {
            runCatching { scheduleWorkers(this) }
                .onFailure { Log.w(TAG, "Deferring worker scheduling until WorkManager is ready", it) }
            startPollingLoop()
        }
        return START_STICKY
    }

    private val screenOnReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val exitedAt = getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
                .getLong(MainActivity.PREF_OPERATOR_EXIT_AT, 0L)
            if (System.currentTimeMillis() - exitedAt > OPERATOR_EXIT_GRACE_MS) keepPlayerInFrontAfterStart()
        }
    }

    /**
     * One launch at boot was not enough to end up ON the player.
     *
     * The home screen can come up after the boot broadcast -- Google TV's launcher finishes
     * loading seconds later and lands on top of whatever opened first. So for two minutes the
     * player is brought back whenever it is not on screen, and left alone once an operator
     * exits deliberately. A player already in front is not touched.
     */
    private fun keepPlayerInFrontAfterStart() {
        bootFollowUp?.cancel()
        val startedAt = System.currentTimeMillis()
        bootFollowUp = serviceScope.launch {
            var waited = 0L
            for (atSeconds in BOOT_FOLLOW_UP_SECONDS) {
                delay((atSeconds - waited) * 1_000L)
                waited = atSeconds
                val exitedAt = getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
                    .getLong(MainActivity.PREF_OPERATOR_EXIT_AT, 0L)
                if (exitedAt >= startedAt) return@launch
                if (!MainActivity.visible) {
                    PlayerLauncher.launch(this@PlaybackService, PlayerLauncher.WARM_RESTART_MS, reason = "follow_up_${atSeconds}s")
                }
            }
        }
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Relaunching when the player's task is swiped away is what keeps a screen on its
        // adverts -- except straight after an operator chose "Exit to home screen", when it
        // would drag the player back over the launcher they just asked for.
        val exitedAt = getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
            .getLong(MainActivity.PREF_OPERATOR_EXIT_AT, 0L)
        if (System.currentTimeMillis() - exitedAt > OPERATOR_EXIT_GRACE_MS) launchPlayer()
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(screenOnReceiver) }
        bootFollowUp?.cancel()
        realtimeClient?.stop()
        realtimeClient = null
        connectivityWatcher?.stop()
        connectivityWatcher = null
        pollingJob = null
        commandPollJob?.cancel()
        commandPollJob = null
        serviceScope.cancel()
        wakeLock?.takeIf(PowerManager.WakeLock::isHeld)?.release()
        wakeLock = null
        super.onDestroy()
    }

    private fun startRealtimeClient() {
        realtimeClient = RealtimeClient(this) { msg ->
            val type = msg.optString("type")
            val command = msg.optString("command")

            // A screen's socket is subscribed to `screen:<device_id>` AND to `org:<id>` and
            // `group:<id>`, so plenty of what arrives here was addressed to the tenant
            // rather than to this panel. Anything that names a screen is therefore checked
            // against this one before it is obeyed.
            //
            // "Open app on TV" is published to both channels, and the org copy names the
            // device it meant. Without this check every television in the tenant came to
            // the front when an operator pressed the button on one of them -- on an estate
            // running per-screen content that interrupts every other screen to fix one.
            val addressedDevice = msg.optString("device_id").takeIf { it.isNotBlank() }
            if (addressedDevice != null && addressedDevice != DeviceState(this).deviceId) {
                Log.d(TAG, "Ignoring $type/$command addressed to another screen")
                return@RealtimeClient
            }

            when {
                type == "request_screenshot" || command == "request_screenshot" -> ScreenshotManager.requestScreenshot()
                (type == "launch_app" || type == "bring_to_front" || command == "launch_app" || command == "bring_to_front") -> {
                    // Arrives on the screen AND org sockets for one press; act on it once.
                    if (com.olrac.signage.boot.RemoteLaunchGate.allow()) launchPlayer()
                    else Log.d(TAG, "Ignoring a repeat bring_to_front")
                }
                type in setOf("sync", "sync_now", "reload", "reload_playlist", "content_updated", "playlist_updated") ||
                    command in setOf("sync", "sync_now", "reload", "reload_playlist", "content_updated", "playlist_updated") -> {
                    android.util.Log.i("PlaybackService", "Received WS sync event (type=$type command=$command); triggering immediate sync")
                    immediateSyncSignals.trySend(Unit)
                }
                // "deregister" is what the server queues when an operator removes the
                // screen from their fleet; the others are the manual unlink. Same outcome.
                type == "deregister" || command == "deregister" ||
                    type == "reset" || type == "unpair" || command == "reset" || command == "unpair" -> {
                    android.util.Log.w("PlaybackService", "Received WS reset/unpair command; signing out")
                    com.olrac.signage.boot.PlayerLauncher.handleUnpairedOrDeleted(this)
                }
            }
        }
        realtimeClient?.start()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun promoteToForeground() {
        val activityIntent = Intent(this, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            activityIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.mr_icon)
            .setContentTitle(getString(R.string.app_name))
            .setContentText("Signage playback protection is active")
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Playback protection",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Keeps managed signage playback alive"
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun acquireWakeLock() {
        val powerManager = getSystemService(PowerManager::class.java)
        wakeLock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "$packageName:playback"
        ).apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun launchPlayer() {
        // A foreground service is still a background context for activity-start purposes
        // on Android 10+, so a bare startActivity() here is silently dropped on budget
        // OEM firmware. PlayerLauncher routes through an AlarmManager PendingIntent,
        // which the system process dispatches and is therefore permitted.
        PlayerLauncher.launch(this, delayMs = 500L, reason = "service_command")
    }

    private fun isUserUnlocked(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return true
        return getSystemService(UserManager::class.java).isUserUnlocked
    }

    private fun startPollingLoop() {
        if (pollingJob?.isActive == true) return

        connectivityWatcher = ConnectivityWatcher(this) {
            immediateSyncSignals.trySend(Unit)
            // Push the queued proof of play the moment the network is back, rather than
            // waiting out the 15-minute periodic window. After an outage that queue is
            // exactly what the operator is waiting to see. Guarded like scheduleWorkers:
            // WorkManager can still be unavailable this early, and a throw here would crash
            // the service on a network change.
            runCatching { ProofOfPlayWorker.enqueueNow(this) }
        }.also(ConnectivityWatcher::start)

        pollingJob = serviceScope.launch {
            val synchronizer = PlaylistSynchronizer(this@PlaybackService)
            var consecutiveFailures = 0
            while (isActive) {
                val outcome = synchronizer.sync()
                // Cheap when nothing changed: collected locally and only posted on a change or
                // every half hour. Isolated so a report can never disturb sync or heartbeat.
                runCatching {
                    com.olrac.signage.telemetry.DeviceDiagnostics.sendIfDue(
                        this@PlaybackService, DeviceState(this@PlaybackService).deviceId
                    )
                }
                try {
                    HeartbeatReporter.send(this@PlaybackService)
                    ProofOfPlayReporter.flush(this@PlaybackService)
                    ProofOfPlayWorker.enqueueNow(this@PlaybackService)
                } catch (exception: Exception) {
                    Log.d(TAG, "Telemetry heartbeat unavailable", exception)
                }
                val baseDelaySeconds = if (outcome.successful) {
                    consecutiveFailures = 0
                    outcome.intervalSeconds
                } else {
                    val delay = SyncBackoffPolicy.failureDelaySeconds(consecutiveFailures)
                    consecutiveFailures++
                    delay
                }

                // Smear polling and reconnect requests across a ±25% randomized window.
                // Without this, 500 TVs turning on at the same time (e.g. mall power restored)
                // would stay perfectly synced and execute a thundering herd every 60 seconds.
                val jitterMultiplier = kotlin.random.Random.nextDouble(0.75, 1.25)
                val nextDelaySeconds = (baseDelaySeconds * jitterMultiplier).toLong().coerceAtLeast(1L)

                val networkOrManualSignal = withTimeoutOrNull(nextDelaySeconds * 1_000L) {
                    immediateSyncSignals.receive()
                    true
                } ?: false
                if (networkOrManualSignal) consecutiveFailures = 0
            }
        }

        startCommandPollLoop()
    }

    /**
     * A second, much faster poll that exists only to collect remote commands.
     *
     * "Open app on TV" queues a command server-side and the screen picks it up on its next
     * call. Both calls that carry one -- sync and heartbeat -- were on the same 60s loop
     * with +/-25% jitter, so pressing the button meant waiting up to 75 seconds with the
     * dashboard showing nothing in the meantime. Long enough that the honest read is "the
     * button does not work", which is what it was reported as.
     *
     * The server cannot push: a TV behind a venue's NAT holds no connection, which is why
     * commands are queued rather than sent. Polling faster is the whole lever available,
     * so this sends the same heartbeat on a short cycle and leaves the expensive work --
     * the playlist sync, the media downloads, the proof-of-play flush -- where it was.
     *
     * A heartbeat is a small POST that updates last_seen and takes any pending command, so
     * four times as many of them is cheap, and it sharpens the online/offline reading as a
     * side effect. It backs off on failure for the same reason the sync loop does: a screen
     * whose venue has lost its line must not retry every fifteen seconds for ever.
     */
    private fun startCommandPollLoop() {
        if (commandPollJob?.isActive == true) return
        commandPollJob = serviceScope.launch {
            var consecutiveFailures = 0
            while (isActive) {
                val delaySeconds = if (consecutiveFailures == 0) {
                    COMMAND_POLL_SECONDS
                } else {
                    SyncBackoffPolicy.failureDelaySeconds(consecutiveFailures - 1)
                }
                // Jittered for the same reason the sync loop is: a mall coming back on
                // power must not put every screen on the same fifteen-second tick.
                val jitter = kotlin.random.Random.nextDouble(0.75, 1.25)
                delay(((delaySeconds * jitter).toLong().coerceAtLeast(1L)) * 1_000L)
                try {
                    HeartbeatReporter.send(this@PlaybackService)
                    consecutiveFailures = 0
                } catch (exception: Exception) {
                    consecutiveFailures++
                    Log.d(TAG, "Command poll heartbeat unavailable", exception)
                }
            }
        }
    }

    companion object {
        private const val TAG = "PlaybackService"
        private const val OPERATOR_EXIT_GRACE_MS = 30 * 60_000L
        private const val CHANNEL_ID = "playback-protection"
        private const val NOTIFICATION_ID = 1001
        private const val EXTRA_LAUNCH_PLAYER = "launch_player"
        private const val EXTRA_AFTER_BOOT = "after_boot"

        /** Seconds after a boot or screen-on at which the player is put back if it is not in front. */
        private val BOOT_FOLLOW_UP_SECONDS = listOf(5L, 15L, 30L, 60L, 120L)
        private const val ACTION_SYNC_NOW = "com.olrac.signage.action.SYNC_NOW"

        /** How often to ask for a queued remote command. See startCommandPollLoop. */
        private const val COMMAND_POLL_SECONDS = 15

        fun start(context: Context, launchPlayer: Boolean, afterBoot: Boolean = false) {
            val intent = Intent(context, PlaybackService::class.java)
                .putExtra(EXTRA_LAUNCH_PLAYER, launchPlayer)
                .putExtra(EXTRA_AFTER_BOOT, afterBoot)
            ContextCompat.startForegroundService(context, intent)
        }

        fun scheduleWorkers(context: Context) {
            val syncRequest = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES).build()
            val heartbeatRequest =
                PeriodicWorkRequestBuilder<HeartbeatWorker>(15, TimeUnit.MINUTES).build()
            // Without a network constraint every offline run threw, returned Result.retry(),
            // and pushed the upload further into WorkManager's exponential backoff -- up to the
            // 5-hour cap -- which restored connectivity does NOT cancel. So a screen that spent
            // the night offline could sit on a full queue for hours after coming back. Gating on
            // CONNECTED means it simply does not run offline, and WorkManager releases it as soon
            // as the network returns.
            val proofOfPlayRequest =
                PeriodicWorkRequestBuilder<ProofOfPlayWorker>(15, TimeUnit.MINUTES)
                    .setConstraints(
                        Constraints.Builder()
                            .setRequiredNetworkType(NetworkType.CONNECTED)
                            .build()
                    )
                    .build()
            
            WorkManager.getInstance(context).apply {
                enqueueUniquePeriodicWork(
                    "sync",
                    ExistingPeriodicWorkPolicy.KEEP,
                    syncRequest
                )
                enqueueUniquePeriodicWork(
                    "heartbeat",
                    ExistingPeriodicWorkPolicy.KEEP,
                    heartbeatRequest
                )
                // UPDATE, not KEEP: already-deployed screens have a constraint-less
                // proof_of_play registered, and KEEP would leave them on it forever.
                enqueueUniquePeriodicWork(
                    "proof_of_play",
                    ExistingPeriodicWorkPolicy.UPDATE,
                    proofOfPlayRequest
                )
            }
        }

        fun requestImmediateSync(context: Context) {
            val intent = Intent(context, PlaybackService::class.java).setAction(ACTION_SYNC_NOW)
            ContextCompat.startForegroundService(context, intent)
        }
    }
}
