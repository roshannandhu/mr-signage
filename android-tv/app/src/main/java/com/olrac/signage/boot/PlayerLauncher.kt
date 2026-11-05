package com.olrac.signage.boot

import android.app.ActivityOptions
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.core.app.NotificationCompat
import com.olrac.signage.R
import com.olrac.signage.data.AppDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.launch

/**
 * Brings the player to the foreground after boot, or on remote "bring_to_front" commands.
 *
 * Employs a multi-layer strategy:
 *  1. Full-Screen Intent Notification: On Android 10-14, Foreground Services can reliably
 *     pop an activity to the foreground using high-priority fullScreenIntent.
 *  2. AlarmManager Dispatch: Routed via AlarmManager PendingIntent which is dispatched by
 *     the system process. Includes Android 14+ MODE_BACKGROUND_ACTIVITY_START_ALLOWED.
 *  3. Direct startActivity: For Device Owner / unconstrained OEMs.
 */
object PlayerLauncher {
    private const val TAG = "PlayerLauncher"
    private const val CHANNEL_BRING_TO_FRONT = "olrac_bring_to_front"
    private const val NOTIFICATION_ID_BRING_TO_FRONT = 9991

    /** Fast system settle before grabbing the foreground on boot. */
    const val BOOT_SETTLE_MS = 2_000L

    /** Immediate delay when the app is already alive (supervisor restart, package replaced). */
    const val WARM_RESTART_MS = 1_000L

    /** Until when relaunches are held (elapsedRealtime), while a system dialog the player opened is up. */
    @Volatile private var holdUntil = 0L

    /**
     * The player opened a system dialog -- the "use MR Signage as your home app?" question -- and it
     * must survive until answered.
     *
     * That dialog sits in the player's task, and the player is singleTask: bringing it to the
     * front clears everything above it, the dialog included. Any relaunch did exactly that -- a
     * boot follow-up, "Open app on TV", the supervisor, or an alarm or full-screen notification
     * queued a moment earlier. Reproduced on the Android TV emulator: RequestRoleActivity opened,
     * then closed 1 s later by a queued launch. So launches are held while it is open, and any
     * already queued are withdrawn.
     */
    fun holdWhileSystemDialogOpen(context: Context, maxMs: Long = 120_000L) {
        holdUntil = SystemClock.elapsedRealtime() + maxMs
        withdrawQueuedLaunches(context)
    }

    fun releaseHold() {
        holdUntil = 0L
    }

    /**
     * The installer opened a Settings page from the setup gate and is standing at the TV.
     *
     * Every relaunch path runs through [launch] -- the boot follow-up in PlaybackService, the
     * watchdog, the supervisor, a remote "Open app on TV" -- so holding it here is what stops
     * the player yanking itself back over Settings mid-toggle. Held in prefs rather than in
     * memory because the process can be killed while the operator is in Settings, and an
     * in-memory flag would come back cleared and start fighting them again.
     *
     * Queued work is withdrawn as well: an alarm or full-screen notification posted a moment
     * before the hold would otherwise still land.
     */
    fun holdForSetup(context: Context, windowMs: Long) {
        context.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).edit()
            .putLong(PREF_SETUP_AWAITING_UNTIL, System.currentTimeMillis() + windowMs).apply()
        withdrawQueuedLaunches(context)
    }

    /** The operator is back on the player: normal protection resumes immediately. */
    fun releaseSetupHold(context: Context) {
        context.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).edit()
            .remove(PREF_SETUP_AWAITING_UNTIL).apply()
    }

    private fun setupHoldActive(context: Context): Boolean =
        System.currentTimeMillis() < context.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
            .getLong(PREF_SETUP_AWAITING_UNTIL, 0L)

    private fun withdrawQueuedLaunches(context: Context) {
        runCatching {
            (context.getSystemService(Context.ALARM_SERVICE) as AlarmManager)
                .cancel(createLaunchPendingIntent(context, playerIntent(context)))
        }
        runCatching {
            (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .cancel(NOTIFICATION_ID_BRING_TO_FRONT)
        }
    }

    fun launch(context: Context, delayMs: Long = BOOT_SETTLE_MS, reason: String = "unspecified") {
        if (SystemClock.elapsedRealtime() < holdUntil) {
            Log.i(TAG, "Launch held while a system dialog is open (reason=$reason)")
            return
        }
        if (setupHoldActive(context)) {
            Log.i(TAG, "Launch held while the installer is in Settings finishing setup (reason=$reason)")
            return
        }
        if (!com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(context) &&
            reason != "remote_lock" && !reason.startsWith("manual")
        ) {
            Log.i(TAG, "Screen is unlocked; ignoring background launch request (reason=$reason)")
            return
        }
        warnIfBackgroundStartsWillBeRefused(context, reason)
        val intent = playerIntent(context)
        context.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).edit()
            .putString(PREF_LAST_LAUNCH, "${System.currentTimeMillis()} reason=$reason overlay=${canStartFromBackground(context)}")
            .apply()

        // 1. Direct startActivity. Allowed from the background on Android 10+ only for a device
        //    owner or an app granted "Display over other apps" -- which is why that permission,
        //    not an accessibility service, is what makes a TV reopen the player after a restart.
        attemptDirectStart(context, intent, reason)

        // 3. High-priority Full-Screen Intent Notification (guaranteed foreground takeover on Android 10-14)
        triggerFullScreenNotification(context, intent, reason)

        // 4. System-level AlarmManager dispatch (with Android 14+ background launch allowance)
        scheduleViaAlarm(context, intent, delayMs, reason)
    }

    /**
     * Whether this panel can actually raise its own window.
     */
    fun canStartFromBackground(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.Q || Settings.canDrawOverlays(context) || isEffectiveHome(context)

    /**
     * Whether Home actually opens this player -- not merely whether it holds the home role.
     *
     * On Android TV the two differ. The TV launcher declares its Home entry at priority 2 and
     * Android always prefers the higher priority, so an app granted the role still loses: proven
     * on the Android TV emulator, where the role went to MR Signage and both Home and the resolver
     * still chose com.google.android.tvlauncher.
     */
    fun isEffectiveHome(context: Context): Boolean = runCatching {
        context.packageManager.resolveActivity(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME),
            android.content.pm.PackageManager.MATCH_DEFAULT_ONLY,
        )?.activityInfo?.packageName == context.packageName
    }.getOrDefault(false)

    private fun warnIfBackgroundStartsWillBeRefused(context: Context, reason: String) {
        if (canStartFromBackground(context)) return
        Log.w(
            TAG,
            "SYSTEM_ALERT_WINDOW is not granted (reason=$reason). Full-screen notification & alarm fallback active.",
        )
    }

    fun playerIntent(context: Context): Intent = Intent(Intent.ACTION_MAIN).apply {
        component = ComponentName(context.packageName, "com.olrac.signage.MainActivity")
        addFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK or
            Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or
            Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED
        )
    }

    private fun createLaunchPendingIntent(context: Context, intent: Intent): PendingIntent {
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)

        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            val options = ActivityOptions.makeBasic().apply {
                setPendingIntentBackgroundActivityStartMode(
                    ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOWED
                )
            }.toBundle()
            PendingIntent.getActivity(context, REQUEST_BOOT_LAUNCH, intent, flags, options)
        } else {
            PendingIntent.getActivity(context, REQUEST_BOOT_LAUNCH, intent, flags)
        }
    }

    private fun triggerFullScreenNotification(context: Context, intent: Intent, reason: String) {
        try {
            val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val channel = NotificationChannel(
                    CHANNEL_BRING_TO_FRONT,
                    "Screen Bring To Front",
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = "Brings signage player to foreground when requested remotely"
                    setSound(null, null)
                    enableVibration(false)
                }
                notificationManager.createNotificationChannel(channel)
            }

            val pending = createLaunchPendingIntent(context, intent)
            val notification = NotificationCompat.Builder(context, CHANNEL_BRING_TO_FRONT)
                .setSmallIcon(R.drawable.mr_icon)
                .setContentTitle(context.getString(R.string.app_name))
                .setContentText("Activating player display...")
                .setPriority(NotificationCompat.PRIORITY_MAX)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setFullScreenIntent(pending, true)
                .setAutoCancel(true)
                .setOngoing(false)
                .build()

            notificationManager.notify(NOTIFICATION_ID_BRING_TO_FRONT, notification)
            Log.i(TAG, "Full-screen launch notification posted (reason=$reason)")

            // Auto-clear the notification after 3 seconds so the notification tray stays clean
            Handler(Looper.getMainLooper()).postDelayed({
                try {
                    notificationManager.cancel(NOTIFICATION_ID_BRING_TO_FRONT)
                } catch (_: Exception) {}
            }, 3000L)
        } catch (exception: Exception) {
            Log.e(TAG, "Failed to post full-screen notification (reason=$reason)", exception)
        }
    }

    private fun scheduleViaAlarm(context: Context, intent: Intent, delayMs: Long, reason: String) {
        try {
            val pending = createLaunchPendingIntent(context, intent)
            val alarms = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            val triggerTime = SystemClock.elapsedRealtime() + delayMs

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                try {
                    alarms.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerTime, pending)
                } catch (_: SecurityException) {
                    alarms.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerTime, pending)
                }
            } else {
                alarms.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerTime, pending)
            }
            Log.i(TAG, "Launch alarm scheduled in ${delayMs}ms (reason=$reason)")
        } catch (exception: Exception) {
            Log.e(TAG, "Alarm scheduling failed (reason=$reason)", exception)
        }
    }

    private fun attemptDirectStart(context: Context, intent: Intent, reason: String) {
        try {
            context.startActivity(intent)
            Log.i(TAG, "Direct startActivity accepted (reason=$reason)")
        } catch (exception: Exception) {
            Log.d(TAG, "Direct startActivity refused, relying on full-screen intent/alarm (reason=$reason)")
        }
    }

    fun handleUnpairedOrDeleted(context: Context) {
        val appContext = context.applicationContext
        Log.w(TAG, "Device unpair/deletion initiated. Clearing state and returning to setup.")
        try {
            // 1. Clear pairing state & device secret.
            //
            // clearWorkspace rather than clearPairing: this path is also reached when an
            // operator REMOVES the screen from their fleet, not only when it is unlinked
            // for re-pairing. clearPairing deliberately keeps the cached playlist so the
            // same panel resumes after re-linking, which is wrong here -- the screen no
            // longer belongs to that tenant, and their ads must not keep playing on it.
            com.olrac.signage.data.DeviceState(appContext).clearWorkspace()
            com.olrac.signage.network.ApiClient.clearToken()

            // 2. Clear the cached playlist AND the media it points at, in the background.
            //    Dropping only the rows leaves the downloaded files on disk, where they
            //    survive until something else happens to sweep them.
            @Suppress("OPT_IN_USAGE")
            GlobalScope.launch(Dispatchers.IO) {
                try {
                    AppDatabase.getDatabase(appContext).playlistDao().replaceAll(emptyList())
                    appContext.filesDir.listFiles()
                        ?.filter { it.name.startsWith("content-") }
                        ?.forEach { it.delete() }
                } catch (_: Exception) {}
            }

            // 3. Stop PlaybackService
            try {
                appContext.stopService(Intent(appContext, com.olrac.signage.service.PlaybackService::class.java))
            } catch (_: Exception) {}

            // 4. Launch MainActivity with clean task flags to show Google Sign-In / Pairing
            val intent = Intent(appContext, com.olrac.signage.MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
                putExtra("show_signin", true)
            }
            appContext.startActivity(intent)
        } catch (e: Exception) {
            Log.e(TAG, "Error while unpairing device", e)
        }
    }

    private const val REQUEST_BOOT_LAUNCH = 1001
    const val PREF_LAST_LAUNCH = "last_launch"

    /**
     * Until when relaunches stand down because the setup gate sent the operator into Settings.
     * MainActivity writes the same key; it lives here because [launch] is the choke point that
     * has to honour it, and boot-layer code must be able to read it without an Activity.
     */
    const val PREF_SETUP_AWAITING_UNTIL = "setup_awaiting_until"
}
