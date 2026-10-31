package com.olrac.signage.receivers

import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.Context
import android.content.Intent
import android.os.PersistableBundle
import android.util.Log
import com.olrac.signage.data.DeviceState
import com.olrac.signage.network.ApiClient
import com.olrac.signage.network.EnrollRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class SignageDeviceAdminReceiver : DeviceAdminReceiver() {
    override fun onEnabled(context: Context, intent: Intent) {
        super.onEnabled(context, intent)
        Log.d("DeviceAdmin", "Device owner enabled")
    }

    override fun onDisabled(context: Context, intent: Intent) {
        super.onDisabled(context, intent)
        Log.d("DeviceAdmin", "Device owner disabled")
    }

    override fun onProfileProvisioningComplete(context: Context, intent: Intent) {
        super.onProfileProvisioningComplete(context, intent)
        Log.d("DeviceAdmin", "Profile provisioning complete")

        val bundle = intent.getParcelableExtra<PersistableBundle>(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE)
        if (bundle != null) {
            val apiBaseUrl = bundle.getString("api_base_url")
            val enrollmentToken = bundle.getString("enrollment_token")

            val deviceState = DeviceState(context)
            if (!apiBaseUrl.isNullOrBlank()) {
                deviceState.setApiBaseUrlOverride(apiBaseUrl)
                Log.d("DeviceAdmin", "Set api_base_url from QR: $apiBaseUrl")
            }

            if (!enrollmentToken.isNullOrBlank()) {
                Log.d("DeviceAdmin", "Found enrollment token, attempting auto-enrollment")
                CoroutineScope(Dispatchers.IO).launch {
                    try {
                        val deviceId = deviceState.deviceId
                        val request = EnrollRequest(
                            device_id = deviceId,
                            enrollment_token = enrollmentToken,
                            installation_id = deviceState.installationId
                        )
                        val response = ApiClient.service(context).enroll(request)
                        if (response.isSuccessful) {
                            val body = response.body()
                            if (body != null) {
                                // The server keeps only a hash, so this is the one time the secret
                                // exists. Dropping it left the screen unable to authenticate, and
                                // once legacy device auth is closed a heartbeat 401 signs it out.
                                deviceState.setDeviceSecret(body.device_secret)
                                deviceState.markPaired(body.screen_id.toString()) // fallback screen name as ID, or we could fetch the name. Actually, we just need to set isPaired = true
                                // Actually, let's just mark paired with generic name since enroll doesn't return screen name directly but it returns screen_id.
                                deviceState.markPaired("Screen ${body.screen_id}")
                                Log.i("DeviceAdmin", "Auto-enrollment successful! Screen ID: ${body.screen_id}")
                            }
                        } else {
                            Log.e("DeviceAdmin", "Auto-enrollment failed: ${response.code()}")
                        }
                    } catch (e: Exception) {
                        Log.e("DeviceAdmin", "Exception during auto-enrollment", e)
                    }
                }
            }
        }
    }
}
