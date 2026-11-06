package com.olrac.signage.device

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.util.Log
import com.olrac.signage.receivers.SignageDeviceAdminReceiver

object DeviceOwnerManager {
    private const val TAG = "DeviceOwnerManager"
    private const val PREFS = "signage_prefs"
    private const val KEY_EXIT_LAUNCHER = "kiosk_exit_launcher_package"

    private fun homeFilter() = IntentFilter(Intent.ACTION_MAIN).apply {
        addCategory(Intent.CATEGORY_HOME)
        addCategory(Intent.CATEGORY_DEFAULT)
        priority = IntentFilter.SYSTEM_HIGH_PRIORITY
    }

    /**
     * Checks whether this app is the device owner.
     */
    fun isDeviceOwner(context: Context): Boolean {
        val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
        return dpm.isDeviceOwnerApp(context.packageName)
    }

    /**
     * Undo the parts of kiosk mode that would stop an operator actually leaving the player:
     * the forced home screen (every HOME press reopened the player) and the disabled status bar.
     * Lock task is stopped by the activity itself. `applyKioskPolicy` puts all of it back the
     * next time the player comes to the front, so this is an exit, not a way to disable kiosk.
     */
    fun releaseKioskPolicy(context: Context, homeLauncher: ComponentName? = null) {
        if (!isDeviceOwner(context)) return
        val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val adminName = ComponentName(context, SignageDeviceAdminReceiver::class.java)
        try {
            dpm.clearPackagePersistentPreferredActivities(adminName, context.packageName)
            // Clearing ours is not enough when this player also holds the HOME role: Home would
            // still come straight back here. Point Home at the launcher the operator exited to,
            // and remember it so applyKioskPolicy can take that back off.
            if (homeLauncher != null) {
                dpm.addPersistentPreferredActivity(adminName, homeFilter(), homeLauncher)
                context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                    .putString(KEY_EXIT_LAUNCHER, homeLauncher.packageName).apply()
            }
            dpm.setStatusBarDisabled(adminName, false)
            dpm.setKeyguardDisabled(adminName, false)
            Log.i(TAG, "Kiosk policy released for an operator exit (home -> ${homeLauncher?.packageName})")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to release kiosk policy", e)
        }
    }

    /**
     * Let the maintenance screen's buttons open system screens while kiosk stays on.
     *
     * Lock task only lets allowlisted packages start. Settings, the display-over-apps page and the
     * launcher-role dialog live in other packages, so they are resolved from the intents those
     * buttons actually fire and added -- plus the usual package names, in case this app cannot
     * see them. applyKioskPolicy puts the allowlist back to this player alone.
     */
    fun allowMaintenanceApps(context: Context) {
        if (!isDeviceOwner(context)) return
        val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val adminName = ComponentName(context, SignageDeviceAdminReceiver::class.java)
        val pm = context.packageManager
        val resolved = listOf(
            Intent(android.provider.Settings.ACTION_SETTINGS),
            Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION),
            Intent("android.app.role.action.REQUEST_ROLE"),
        ).mapNotNull { intent ->
            runCatching { pm.resolveActivity(intent, 0)?.activityInfo?.packageName }.getOrNull()
        }.filter { it != "android" }
        val allowed = (listOf(context.packageName) + resolved + KNOWN_SETTINGS_PACKAGES).distinct()
        try {
            dpm.setLockTaskPackages(adminName, allowed.toTypedArray())
            Log.i(TAG, "Maintenance lock-task allowlist: $allowed")
        } catch (e: Exception) {
            Log.e(TAG, "Could not widen the lock-task allowlist for maintenance", e)
        }
    }

    private val KNOWN_SETTINGS_PACKAGES = listOf(
        "com.android.settings", "com.android.tv.settings",
        "com.android.permissioncontroller", "com.google.android.permissioncontroller",
    )

    /**
     * Call on first run when device owner to lock down the device into kiosk mode.
     */
    fun applyKioskPolicy(context: Context) {
        if (!isDeviceOwner(context)) {
            Log.w(TAG, "Not applying kiosk policy: App is not device owner")
            return
        }

        val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val adminName = ComponentName(context, SignageDeviceAdminReceiver::class.java)

        try {
            // 1. LockTask Mode: Allow only MR Signage
            dpm.setLockTaskPackages(adminName, arrayOf(context.packageName))
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P) {
                try {
                    dpm.setLockTaskFeatures(adminName, DevicePolicyManager.LOCK_TASK_FEATURE_NONE)
                } catch (e: Exception) {
                    Log.w(TAG, "Could not set LOCK_TASK_FEATURE_NONE", e)
                }
            }
            Log.d(TAG, "Lock task packages and features locked down")

            // 2. Persistent preferred activity for HOME intent
            // Aggressively clear persistent preferred activities for all other home launchers
            runCatching {
                val pm = context.packageManager
                val homeIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
                val resolveInfos = pm.queryIntentActivities(homeIntent, android.content.pm.PackageManager.MATCH_DEFAULT_ONLY)
                for (info in resolveInfos) {
                    val pkg = info.activityInfo.packageName
                    if (pkg != context.packageName) {
                        dpm.clearPackagePersistentPreferredActivities(adminName, pkg)
                    }
                }
                val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                prefs.getString(KEY_EXIT_LAUNCHER, null)?.let { launcherPackage ->
                    dpm.clearPackagePersistentPreferredActivities(adminName, launcherPackage)
                    prefs.edit().remove(KEY_EXIT_LAUNCHER).apply()
                }
            }.onFailure { Log.w(TAG, "Could not clear previous home persistent preferred activities", it) }

            // Clear our own previous registration before re-adding with highest priority
            dpm.clearPackagePersistentPreferredActivities(adminName, context.packageName)
            val activityName = ComponentName(context, com.olrac.signage.MainActivity::class.java)
            dpm.addPersistentPreferredActivity(adminName, homeFilter(), activityName)
            Log.d(TAG, "Persistent preferred activity set to MainActivity")

            // 3. Disable keyguard and status bar completely
            dpm.setKeyguardDisabled(adminName, true)
            dpm.setStatusBarDisabled(adminName, true)
            Log.d(TAG, "Keyguard and status bar disabled")

            // 4. Auto-grant runtime permissions
            dpm.setPermissionPolicy(adminName, DevicePolicyManager.PERMISSION_POLICY_AUTO_GRANT)
            val permissionsToGrant = arrayOf(
                android.Manifest.permission.ACCESS_FINE_LOCATION,
                android.Manifest.permission.ACCESS_COARSE_LOCATION,
                "android.permission.NEARBY_WIFI_DEVICES"
            )
            for (p in permissionsToGrant) {
                try {
                    dpm.setPermissionGrantState(adminName, context.packageName, p, DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED)
                } catch (_: Exception) {}
            }
            Log.d(TAG, "Permission policy set to auto grant")

            // 5. Block uninstalls
            dpm.setUninstallBlocked(adminName, context.packageName, true)
            Log.d(TAG, "Uninstall blocked")

            Log.i(TAG, "Kiosk policy applied successfully")
        } catch (e: SecurityException) {
            Log.e(TAG, "SecurityException while applying kiosk policy. Ensure app is truly device owner.", e)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to apply kiosk policy", e)
        }
    }
}
