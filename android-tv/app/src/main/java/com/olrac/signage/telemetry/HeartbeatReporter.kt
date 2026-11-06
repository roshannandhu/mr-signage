package com.olrac.signage.telemetry

import android.content.Context
import android.os.Environment
import android.os.StatFs
import com.olrac.signage.BuildConfig
import com.olrac.signage.device.DeviceOwnerManager
import com.olrac.signage.network.ApiClient
import com.olrac.signage.network.HeartbeatRequest
import java.util.Locale

object HeartbeatReporter {
    /**
     * Whether a heartbeat answer means this screen no longer exists on the server.
     *
     * Only a 404 does. A 401 or 403 means the token minted from the device secret lapsed or
     * was rejected -- ApiClient has already dropped it and the next beat mints another.
     * Treating those as "deleted" wiped the pairing, the secret, the playlist and every
     * downloaded file over a momentary auth failure, and rotating the server's SECRET_KEY
     * invalidates every token at once, so it would have factory-reset the whole fleet.
     * PlaylistSynchronizer reached the same conclusion; this path had been missed.
     */
    fun signsOutOn(code: Int): Boolean = code == 404

    suspend fun send(context: Context, suppliedSnapshot: PlaybackSnapshot? = null): Boolean {
        val appContext = context.applicationContext
        val preferences = appContext.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
        // Through DeviceState, which derives and persists the id on first access. Reading
        // the preference directly returned null whenever the service started before
        // MainActivity had ever run -- a boot straight into the player -- and the screen
        // then reported no heartbeat at all, so the fleet list showed it permanently
        // offline while it was sitting there playing.
        val deviceId = com.olrac.signage.data.DeviceState(appContext).deviceId
        val snapshot = suppliedSnapshot ?: PlaybackTelemetry(appContext).snapshot()

        val stat = StatFs(Environment.getDataDirectory().path)
        val bytesTotal = stat.blockSizeLong * stat.blockCountLong
        val bytesAvailable = stat.blockSizeLong * stat.availableBlocksLong
        val usedGb = (bytesTotal - bytesAvailable) / (1024.0 * 1024.0 * 1024.0)
        val totalGb = bytesTotal / (1024.0 * 1024.0 * 1024.0)

        val tSent = System.currentTimeMillis()
        val updateStatus = preferences.getString("update_status", null)
        val caps = DeviceCapabilities.get(appContext)
        val wifiHelper = com.olrac.signage.network.WifiManagerHelper(appContext)
        val wifiSsid = wifiHelper.currentConnectedSsid
        val wifiBars = if (wifiSsid != null) wifiHelper.currentSignalBars else null
        val response = ApiClient.service(appContext).heartbeat(
            HeartbeatRequest(
                device_id = deviceId,
                device_version = BuildConfig.VERSION_NAME,
                // Reported every beat rather than once at pairing: a panel can be provisioned
                // as device owner long after it was first claimed, and the fleet has to
                // notice without anyone re-pairing it.
                device_owner = DeviceOwnerManager.isDeviceOwner(appContext),
                storage_used = String.format(Locale.US, "%.1f GB / %.1f GB", usedGb, totalGb),
                playback_state = snapshot.state,
                current_item_id = snapshot.currentItemId,
                last_error = snapshot.lastError,
                app_version = BuildConfig.VERSION_NAME,
                version_code = BuildConfig.VERSION_CODE,
                update_status = updateStatus,
                screen_width = caps.screen_width,
                screen_height = caps.screen_height,
                refresh_rate = caps.refresh_rate,
                orientation = caps.orientation,
                total_ram_mb = caps.total_ram_mb,
                available_ram_mb = caps.available_ram_mb,
                total_storage_mb = caps.total_storage_mb,
                free_storage_mb = caps.free_storage_mb,
                supported_video_codecs = caps.supported_video_codecs,
                max_decode_width = caps.max_decode_width,
                max_decode_height = caps.max_decode_height,
                manufacturer = caps.manufacturer,
                model = caps.model,
                android_version = caps.android_version,
                sdk_int = caps.sdk_int,
                network_type = caps.network_type,
                wifi_ssid = wifiSsid,
                wifi_signal_bars = wifiBars,
                timezone = caps.timezone,
                is_locked = com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(appContext)
            )
        )
        
        val rtt = System.currentTimeMillis() - tSent
        if (signsOutOn(response.code())) {
            android.util.Log.w("HeartbeatReporter", "Screen removed on the server (HTTP ${response.code()}); signing out")
            com.olrac.signage.boot.PlayerLauncher.handleUnpairedOrDeleted(appContext)
            return false
        }
        if (response.code() == 401 || response.code() == 403) {
            android.util.Log.w("HeartbeatReporter", "Auth rejected (HTTP ${response.code()}); re-authenticating on the next beat")
            return false
        }
        if (response.isSuccessful) {
            response.body()?.let { body ->
                val edit = preferences.edit()
                body.server_time_ms?.let { serverTime ->
                    val offset = serverTime - (tSent + rtt / 2)
                    edit.putLong("server_time_offset_ms", offset)
                }
                body.screen_id?.let { edit.putInt("screen_id", it) }
                body.organization_id?.let { edit.putInt("organization_id", it) }
                edit.apply()

                // If the server's credentials_version is strictly newer than what we last
                // synced, the device has not yet received the updated PIN via a full sync.
                // Trigger one immediately so the "Sync Pending" badge clears within seconds
                // rather than waiting for the next scheduled polling tick.
                val serverCredsVersion = body.credentials_version
                if (serverCredsVersion != null) {
                    val localCredsVersion = com.olrac.signage.data.DeviceState(appContext).credentialsVersion
                    if (serverCredsVersion > localCredsVersion) {
                        android.util.Log.i(
                            "HeartbeatReporter",
                            "Credentials drift detected via heartbeat: local=$localCredsVersion server=$serverCredsVersion; triggering immediate sync"
                        )
                        com.olrac.signage.service.PlaybackService.requestImmediateSync(appContext)
                    }
                }

                // Authoritative kiosk lock state synchronization from heartbeat
                val serverKioskLock = body.kiosk_lock
                if (serverKioskLock != null) {
                    val localKioskLock = com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(appContext)
                    if (serverKioskLock != localKioskLock) {
                        android.util.Log.i(
                            "HeartbeatReporter",
                            "Kiosk lock drift detected via heartbeat: local=$localKioskLock server=$serverKioskLock; updating"
                        )
                        com.olrac.signage.MainActivity.onKioskLockChanged(appContext, serverKioskLock)
                    }
                }

                // See PlaylistSynchronizer for why both fields are merged: pending_commands
                // carries every command still within its hold time, pending_command is the
                // first of those for an older server that has not been upgraded.
                val commands = com.olrac.signage.sync.PendingCommands.merge(body.pending_command, body.pending_commands)
                for (cmd in commands) {
                    if (cmd == "check_update") {
                        // The heartbeat carries no update offer, so fetch the full sync that
                        // does, now rather than at the next minute.
                        com.olrac.signage.sync.UpdateManager.requestUpdateCheck(appContext)
                        com.olrac.signage.service.PlaybackService.requestImmediateSync(appContext)
                    }
                    if (cmd == "remote_unlock") {
                        appContext.getSharedPreferences("signage_prefs", android.content.Context.MODE_PRIVATE)
                            .edit()
                            .putBoolean("kiosk_lock", false)
                            .commit()
                        android.util.Log.i("HeartbeatReporter", "Received remote_unlock command from server; unlocking kiosk immediately")
                        com.olrac.signage.MainActivity.onKioskLockChanged(appContext, false)
                    }
                    if (cmd == "remote_lock") {
                        appContext.getSharedPreferences("signage_prefs", android.content.Context.MODE_PRIVATE)
                            .edit()
                            .putBoolean("kiosk_lock", true)
                            .commit()
                        android.util.Log.i("HeartbeatReporter", "Received remote_lock command from server; locking kiosk immediately")
                        com.olrac.signage.MainActivity.onKioskLockChanged(appContext, true)
                        com.olrac.signage.boot.PlayerLauncher.launch(appContext, delayMs = 0L, reason = "remote_lock")
                    }
                    if ((cmd == "bring_to_front" || cmd == "launch_app") && com.olrac.signage.boot.RemoteLaunchGate.allow()) {
                        android.util.Log.i("HeartbeatReporter", "Received $cmd command from server; bringing app to front")
                        com.olrac.signage.boot.PlayerLauncher.launch(appContext, delayMs = 500L, reason = "heartbeat_command")
                    } else if (cmd == "reset" || cmd == "unpair") {
                        android.util.Log.w("HeartbeatReporter", "Received $cmd command from server; signing out")
                        com.olrac.signage.boot.PlayerLauncher.handleUnpairedOrDeleted(appContext)
                        return false
                    }
                }

                // Flush any accumulated offline or pending play logs immediately on heartbeat
                try {
                    com.olrac.signage.service.ProofOfPlayReporter.flush(appContext)
                } catch (_: Exception) {}
            }
        }
        
        return response.isSuccessful
    }
}
