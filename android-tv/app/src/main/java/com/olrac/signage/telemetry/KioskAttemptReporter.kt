package com.olrac.signage.telemetry

import android.content.Context
import android.util.Log
import com.olrac.signage.data.DeviceState
import com.olrac.signage.network.ApiClient
import com.olrac.signage.network.KioskAttemptRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * Tells the server one local PIN check happened, for the admin audit log only.
 *
 * Fire-and-forget like every other telemetry report here: the PIN check itself already
 * ran, with no network involved, before this is called -- that is the whole point of
 * caching the PIN on the device. A failed report just means this one attempt is invisible
 * to a super admin later, never that the TV behaves differently.
 */
object KioskAttemptReporter {
    private const val TAG = "KioskAttemptReporter"
    private val scope = CoroutineScope(Dispatchers.IO + Job())

    fun report(context: Context, success: Boolean) {
        val appContext = context.applicationContext
        val deviceId = DeviceState(appContext).deviceId
        scope.launch {
            try {
                ApiClient.service(appContext).reportKioskAttempt(deviceId, KioskAttemptRequest(success))
            } catch (e: Exception) {
                Log.d(TAG, "Could not report a kiosk attempt (offline is expected)", e)
            }
        }
    }
}
