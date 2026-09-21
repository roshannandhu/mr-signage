package com.olrac.signage.telemetry

import android.app.ActivityManager
import android.app.AlarmManager
import android.app.AppOpsManager
import android.app.NotificationManager
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import androidx.core.app.NotificationManagerCompat
import com.olrac.signage.BuildConfig
import com.olrac.signage.device.DeviceOwnerManager
import com.olrac.signage.network.ApiClient
import com.olrac.signage.network.DiagnosticsRequest

/**
 * What this TV can say about itself that nobody standing in front of it is there to read.
 *
 * Screens are installed in venues, far from support. Whether the player can bring itself back
 * after a restart is decided entirely by device state -- how the app was installed, which
 * permissions and roles it holds -- and none of that was visible remotely, so
 * "the TV did not reopen the player after a restart" could only be diagnosed by going there. Every probe is
 * wrapped: a report that throws is worth less than one with a field missing.
 */
object DeviceDiagnostics {
    private const val TAG = "DeviceDiagnostics"
    private const val RESEND_AFTER_MS = 30 * 60_000L
    private const val PREFS = "signage_prefs"
    private const val PREF_OVERLAY_SEEN_AT = "overlay_last_seen_allowed_at"

    @Volatile private var lastSent: String? = null
    @Volatile private var lastSentAt = 0L

    fun collect(context: Context): Map<String, Any?> {
        val ctx = context.applicationContext
        val pkg = ctx.packageName
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val report = linkedMapOf<String, Any?>()

        fun probe(name: String, block: () -> Any?) {
            report[name] = try { block() } catch (e: Throwable) { "error: ${e.javaClass.simpleName}: ${e.message?.take(120)}" }
        }

        probe("app_version") { BuildConfig.VERSION_NAME }
        probe("sdk") { Build.VERSION.SDK_INT }
        probe("is_tv") { ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK) }
        probe("device_owner") { DeviceOwnerManager.isDeviceOwner(ctx) }

        // --- does a restart reopen the player? -----------------------------------------------
        // Android 10+ lets an app open itself from the background only as device owner or with
        // "Display over other apps". Both boot timestamps side by side show what really happened
        // on the last restart: broadcast delivered, and player on screen shortly after.
        probe("reopens_after_restart") { com.olrac.signage.boot.PlayerLauncher.canStartFromBackground(ctx) }
        probe("last_boot_action") { prefs.getString(com.olrac.signage.receivers.BootReceiver.PREF_LAST_BOOT_ACTION, null) }
        probe("last_boot_at") { prefs.getLong(com.olrac.signage.receivers.BootReceiver.PREF_LAST_BOOT_AT, 0L).takeIf { it > 0 } }
        probe("player_last_resumed_at") { prefs.getLong(com.olrac.signage.MainActivity.PREF_PLAYER_RESUMED_AT, 0L).takeIf { it > 0 } }
        probe("uptime_ms") { android.os.SystemClock.elapsedRealtime() }
        probe("home_screen_packages") {
            ctx.packageManager.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), PackageManager.MATCH_DEFAULT_ONLY)
                .joinToString(",") { it.activityInfo.packageName }
        }
        probe("last_crash") { prefs.getString(CrashRecorder.PREF_LAST_CRASH, null) }
        probe("default_home_package") {
            ctx.packageManager.resolveActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), PackageManager.MATCH_DEFAULT_ONLY)
                ?.activityInfo?.packageName
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            probe("install_source") {
                val info = ctx.packageManager.getInstallSourceInfo(pkg)
                buildString {
                    append("installing=").append(info.installingPackageName)
                    append(" initiating=").append(info.initiatingPackageName)
                    append(" originating=").append(info.originatingPackageName)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) append(" source=").append(info.packageSource)
                }
            }
        }
        // --- what the player can use to come to the front ----------------------------------
        probe("overlay_allowed") { Settings.canDrawOverlays(ctx) }
        // Allowed, then off again after a restart, means the TV itself resets it at boot.
        probe("overlay_last_seen_allowed_at") {
            if (Settings.canDrawOverlays(ctx)) prefs.edit().putLong(PREF_OVERLAY_SEEN_AT, System.currentTimeMillis()).apply()
            prefs.getLong(PREF_OVERLAY_SEEN_AT, 0L).takeIf { it > 0 }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // 0 allowed, 1 ignored, 2 errored, 3 default (not granted).
            probe("overlay_op_mode") {
                ctx.getSystemService(AppOpsManager::class.java)
                    .unsafeCheckOpRawNoThrow(AppOpsManager.OPSTR_SYSTEM_ALERT_WINDOW, android.os.Process.myUid(), pkg)
            }
            probe("home_role_available") { ctx.getSystemService(RoleManager::class.java).isRoleAvailable(RoleManager.ROLE_HOME) }
            probe("home_role_request_screen") {
                val request = ctx.getSystemService(RoleManager::class.java).createRequestRoleIntent(RoleManager.ROLE_HOME)
                ctx.packageManager.resolveActivity(request, 0)?.activityInfo?.packageName
            }
        }
        probe("low_ram_device") { ctx.getSystemService(ActivityManager::class.java).isLowRamDevice }
        probe("player_visible") { com.olrac.signage.MainActivity.visible }
        probe("home_role_request_result") { prefs.getString(com.olrac.signage.MainActivity.PREF_HOME_ROLE_RESULT, null) }
        probe("last_launch") { prefs.getString(com.olrac.signage.boot.PlayerLauncher.PREF_LAST_LAUNCH, null) }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            probe("home_role_held") { ctx.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_HOME) }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            probe("exact_alarms_allowed") { ctx.getSystemService(AlarmManager::class.java).canScheduleExactAlarms() }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            probe("full_screen_intent_allowed") { ctx.getSystemService(NotificationManager::class.java).canUseFullScreenIntent() }
        }
        // Does the TV still have the watchdog switched on? If this lists our service, restoring it
        // (1.0.30) re-activates with no ADB -- the whole point of bringing it back. connected/unbound
        // times tell "never ran" from "ran and the OEM killed it".
        probe("enabled_accessibility_services") {
            Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
        }
        probe("watchdog_enabled") {
            // Same short-vs-long component spelling that made the setup gate repeat forever:
            // this probe reported false for every TV whose watchdog was in fact running.
            com.olrac.signage.boot.SetupGate.namesService(
                Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES),
                pkg,
                "$pkg.boot.WatchdogAccessibilityService",
            )
        }
        probe("watchdog_connected_at") {
            prefs.getLong(com.olrac.signage.boot.WatchdogAccessibilityService.PREF_CONNECTED_AT, 0L).takeIf { it > 0 }
        }
        // True while a PIN/maintenance or exit window has the watchdog standing down -- so a
        // report that shows the player off-screen with the watchdog enabled is explained.
        probe("watchdog_suppress_active") {
            prefs.getLong(com.olrac.signage.MainActivity.PREF_WATCHDOG_SUPPRESS_UNTIL, 0L) > System.currentTimeMillis()
        }
        probe("watchdog_unbound_at") {
            prefs.getLong(com.olrac.signage.boot.WatchdogAccessibilityService.PREF_UNBOUND_AT, 0L).takeIf { it > 0 }
        }
        probe("notifications_enabled") { NotificationManagerCompat.from(ctx).areNotificationsEnabled() }
        probe("battery_optimisation_ignored") { ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(pkg) }
        probe("lock_task_mode") { ctx.getSystemService(ActivityManager::class.java).lockTaskModeState }
        return report
    }

    /** Send when something changed, and at least every half hour so a stale report is visible. */
    suspend fun sendIfDue(context: Context, deviceId: String?) {
        if (deviceId.isNullOrBlank()) return
        val report = collect(context)
        val serialised = report.toString()
        val now = System.currentTimeMillis()
        if (serialised == lastSent && now - lastSentAt < RESEND_AFTER_MS) return
        try {
            val response = ApiClient.service(context.applicationContext)
                .reportDiagnostics(DiagnosticsRequest(device_id = deviceId, report = report))
            if (response.isSuccessful) {
                lastSent = serialised
                lastSentAt = now
            } else {
                Log.d(TAG, "Diagnostics refused: HTTP ${response.code()}")
            }
        } catch (e: Exception) {
            Log.d(TAG, "Diagnostics unavailable", e)
        }
    }
}
