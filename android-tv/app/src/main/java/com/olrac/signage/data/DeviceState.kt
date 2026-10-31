package com.olrac.signage.data

import android.content.Context
import android.provider.Settings
import java.util.UUID

class DeviceState(context: Context) {
    private val appContext = context.applicationContext
    private val preferences = context.applicationContext.getSharedPreferences(
        PREFERENCES_NAME,
        Context.MODE_PRIVATE
    )

    val deviceId: String
        get() {
            preferences.getString(KEY_DEVICE_ID, null)?.let { return it }

            val androidId = try {
                Settings.Secure.getString(appContext.contentResolver, Settings.Secure.ANDROID_ID)
            } catch (_: Exception) {
                null
            }

            val stableId = if (!androidId.isNullOrBlank() && androidId != "9774d56d682e549c") {
                UUID.nameUUIDFromBytes("olrac_screen_${androidId}".toByteArray()).toString()
            } else {
                UUID.randomUUID().toString()
            }

            preferences.edit().putString(KEY_DEVICE_ID, stableId).commit()
            return stableId
        }

    /**
     * An identity that survives reinstalling the app.
     *
     * [deviceId] lives in SharedPreferences, so clearing app data or reinstalling wipes it
     * and the TV comes back looking like a brand new screen — which left a duplicate ghost
     * in the fleet and consumed another slot of the plan's screen quota. ANDROID_ID is
     * stable for the life of the OS install, so the server can recognise the returning TV
     * and reclaim its existing screen.
     *
     * Falls back to the random id when ANDROID_ID is unavailable, which is no worse than
     * the previous behaviour.
     */
    @Suppress("HardwareIds")
    val installationId: String
        get() = hardwareSerial()?.let { "sn_${it}" } ?: androidId()?.let { "hw_${it}" } ?: deviceId

    /**
     * Which of the identity sources actually answered, reported to the server so an
     * operator can tell WHY a screen did or did not come back as itself.
     *
     * "serial" survives a factory reset. "android_id" survives a reinstall but not a reset.
     * "random" survives nothing -- a screen on that tier will duplicate if it is ever wiped,
     * and the fleet list is where that has to be visible rather than a surprise later.
     */
    val identitySource: String
        get() = when {
            hardwareSerial() != null -> "serial"
            androidId() != null -> "android_id"
            else -> "random"
        }

    private fun androidId(): String? {
        val value = try {
            Settings.Secure.getString(appContext.contentResolver, Settings.Secure.ANDROID_ID)
        } catch (_: Exception) {
            null
        }
        // That literal is a well-known broken value shipped on a batch of devices; treating
        // it as real would collapse every one of them onto a single identity.
        return value?.takeIf { it.isNotBlank() && it != "9774d56d682e549c" }
    }

    /**
     * The hardware serial, tried FIRST because it is the only identifier here that survives
     * a factory reset.
     *
     * The previous ordering had ANDROID_ID first and this as a fallback "for factory
     * resets" -- which never fired, because a reset does not make ANDROID_ID unavailable,
     * it gives it a NEW value. The first branch always won and the panel came back as a
     * brand new screen: a duplicate row, another slot off the quota, and its playlist and
     * play history stranded on a screen that no longer exists.
     *
     * Availability, which decides whether a reset is recoverable at all:
     *   API 26-28  READ_PHONE_STATE is enough (declared in the manifest).
     *   API 29+    restricted to device-owner and privileged apps. This player IS the
     *              device owner on a zero-touch provisioned panel, which is the deployment
     *              this matters for; a sideloaded install on Android 10+ gets a
     *              SecurityException and falls through to ANDROID_ID.
     *
     * Deliberately does NOT mix in Build.MODEL. Every panel in an estate is usually the
     * same model, so it adds no uniqueness -- and including it means a firmware update that
     * edits the model string silently changes the identity of the whole fleet at once.
     */
    private fun hardwareSerial(): String? = try {
        @Suppress("DEPRECATION")
        val raw = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            android.os.Build.getSerial()
        } else {
            android.os.Build.SERIAL
        }
        raw?.takeIf {
            it.isNotBlank() && !it.equals(android.os.Build.UNKNOWN, ignoreCase = true)
        }
    } catch (_: Exception) {
        // SecurityException on API 29+ without device-owner privilege is the normal path
        // on a sideloaded install, not an error worth surfacing.
        null
    }

    val deviceModel: String
        get() = android.os.Build.MODEL ?: "Android TV"

    val manufacturer: String
        get() = android.os.Build.MANUFACTURER ?: "Android"

    val hardwareName: String
        get() {
            val m = manufacturer.trim()
            val mod = deviceModel.trim()
            return if (mod.startsWith(m, ignoreCase = true)) mod else "$m $mod"
        }

    val isPaired: Boolean
        get() = preferences.getBoolean(KEY_IS_PAIRED, false)

    val screenName: String?
        get() = preferences.getString(KEY_SCREEN_NAME, null) ?: hardwareName

    val apiBaseUrlOverride: String?
        get() = preferences.getString(KEY_API_BASE_URL, null)

    /**
     * Gate for the on-TV maintenance screen, issued per screen by the server and refreshed
     * on every sync.
     *
     * Cached locally on purpose: that screen exists to repair a wrong server address, so it
     * has to open when the server cannot be reached. [DEFAULT_MAINTENANCE_PIN] covers a TV
     * that has never completed a sync, which is a device holding no configuration worth
     * protecting yet.
     */
    val maintenancePin: String
        get() = preferences.getString(KEY_MAINTENANCE_PIN, null) ?: DEFAULT_MAINTENANCE_PIN

    fun setMaintenancePin(pin: String) {
        preferences.edit().putString(KEY_MAINTENANCE_PIN, pin).commit()
    }

    /**
     * The operator's master code, if one is set. Cached like the screen's own PIN and for the
     * same reason: the maintenance screen exists to fix connectivity, so it has to open when
     * the server cannot be reached.
     */
    val masterPin: String?
        get() = preferences.getString(KEY_MASTER_PIN, null)?.takeIf { it.isNotBlank() }

    fun setMasterPin(pin: String?) {
        preferences.edit().apply {
            if (pin.isNullOrBlank()) remove(KEY_MASTER_PIN) else putString(KEY_MASTER_PIN, pin)
        }.commit()
    }

    /**
     * Store the screen's opening hours, so playback can observe them offline.
     *
     * Serialised as JSON rather than modelled, because SharedPreferences has no map type
     * and this is read once per playback tick by exactly one caller.
     */
    fun setOperatingHours(mode: String?, windows: Map<String, List<String>>?) {
        val editor = preferences.edit()
        editor.putString(KEY_OPERATING_MODE, mode ?: "always")
        if (windows.isNullOrEmpty()) {
            editor.remove(KEY_OPERATING_HOURS)
        } else {
            editor.putString(KEY_OPERATING_HOURS, org.json.JSONObject(windows as Map<*, *>).toString())
        }
        editor.commit()
    }

    val operatingMode: String
        get() = preferences.getString(KEY_OPERATING_MODE, "always") ?: "always"

    val operatingHours: Map<String, List<String>>?
        get() {
            val raw = preferences.getString(KEY_OPERATING_HOURS, null) ?: return null
            return try {
                val json = org.json.JSONObject(raw)
                json.keys().asSequence().associateWith { day ->
                    val window = json.getJSONArray(day)
                    (0 until window.length()).map(window::getString)
                }
            } catch (_: Exception) {
                // A malformed blob must not stop playback; "always on" is the safe default,
                // matching the server, which treats unparseable windows as not-off.
                null
            }
        }

    /**
     * This screen's own credential, issued once by the route that bound it.
     *
     * Null on a screen paired by an older build of the app; the server still accepts those
     * while ALLOW_LEGACY_DEVICE_AUTH is on, and they pick a secret up the next time they
     * are paired or signed in.
     */
    val deviceSecret: String?
        get() = preferences.getString(KEY_DEVICE_SECRET, null)

    fun setDeviceSecret(secret: String) {
        // commit, not apply: this is the credential the screen needs to come back after a
        // power cut, and losing it to an unflushed write means a manual re-pair on site.
        preferences.edit().putString(KEY_DEVICE_SECRET, secret).commit()
    }

    fun markPaired(screenName: String?) {
        preferences.edit()
            .putBoolean(KEY_IS_PAIRED, true)
            .apply {
                if (!screenName.isNullOrBlank()) {
                    putString(KEY_SCREEN_NAME, screenName)
                }
            }
            // Pairing is tiny, critical state: persist it before reporting success.
            .commit()
    }

    fun clearPairing() {
        preferences.edit()
            .putBoolean(KEY_IS_PAIRED, false)
            .remove(KEY_SCREEN_NAME)
            .remove(KEY_MAINTENANCE_PIN)
            .remove(KEY_DEVICE_SECRET)
            .commit()
    }

    /**
     * Forget the workspace entirely, for a screen the operator removed from the fleet.
     *
     * Wider than [clearPairing], which an operator uses to re-link the SAME panel and so
     * deliberately keeps the cached playlist for continuity. Here the screen no longer
     * belongs to that tenant, so their content must not survive on it: leaving the cached
     * items behind would keep a removed TV playing a customer's ads, which is the thing
     * removing it was meant to stop.
     *
     * [deviceId] is deliberately NOT cleared. It is derived from the hardware and is what
     * lets the panel be recognised if it is paired again; clearing it would make every
     * removal look like a brand-new device and lose the reinstall matching.
     */
    fun clearWorkspace() {
        preferences.edit()
            .putBoolean(KEY_IS_PAIRED, false)
            .remove(KEY_SCREEN_NAME)
            .remove(KEY_MAINTENANCE_PIN)
            .remove(KEY_DEVICE_SECRET)
            .remove("screen_id")
            .remove("organization_id")
            .remove("playlist_updated_at")
            .remove("current_item_id")
            .remove("playback_state")
            .commit()
    }

    fun setApiBaseUrlOverride(baseUrl: String) {
        preferences.edit().putString(KEY_API_BASE_URL, baseUrl).commit()
    }

    companion object {
        const val PREFERENCES_NAME = "signage_prefs"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_IS_PAIRED = "is_paired"
        const val KEY_SCREEN_NAME = "screen_name"
        const val KEY_API_BASE_URL = "api_base_url"
        const val KEY_MAINTENANCE_PIN = "maintenance_pin"
        const val KEY_MASTER_PIN = "master_pin"
        const val KEY_DEVICE_SECRET = "device_secret"
        const val KEY_OPERATING_MODE = "operating_mode"
        const val KEY_OPERATING_HOURS = "operating_hours"
        const val DEFAULT_SCREEN_NAME = "MR Signage Screen"
        // Applies only before the first successful sync; the server's per-screen pin
        // replaces it and every paired screen gets a distinct one.
        const val DEFAULT_MAINTENANCE_PIN = "0000"
    }
}

sealed interface LaunchState {
    data object CheckingLocalState : LaunchState
    data class Playing(val screenName: String? = null) : LaunchState

    /**
     * Default route for an unprovisioned device: the installer signs in with their own
     * MR Signage account and the screen joins that workspace directly. No second person at a
     * dashboard, and no five-minute code to race.
     */
    data class SignIn(
        val error: String? = null,
        val busy: Boolean = false,
        // Defaults false so the button stays hidden until the server says otherwise. A
        // deployment with no Google credentials can only answer /google/start with a 503,
        // and offering a button whose single outcome is an error is worse than not
        // offering it. Shown optimistically would invert that on every offline start-up.
        val googleEnabled: Boolean = false
    ) : LaunchState

    /**
     * Waiting for the installer to approve on their phone.
     *
     * The code shown here is Google's, not ours: on a TV the supported Google flow is the
     * device authorisation grant, so a code still appears. What it removes is typing a
     * password with a D-pad, and needing somebody already signed in at a dashboard.
     */
    data class GoogleSignIn(
        val userCode: String,
        val verificationUrl: String,
        val error: String? = null
    ) : LaunchState

    /** Fallback for when no keyboard is to hand. Reached from a link on [SignIn]. */
    data class Pairing(
        val pairCode: String? = null,
        val connectionMessage: String? = null,
        val issuedAtMs: Long = System.currentTimeMillis(),
        val ttlSeconds: Int = 60
    ) : LaunchState
}

data class RegistrationSnapshot(
    val status: String,
    val pairCode: String?,
    val screenName: String?
)

object LaunchStateResolver {
    /**
     * Whether a "show_signin" request may reset this screen to the sign-in page.
     *
     * The player sends it to itself AFTER wiping its own pairing (PlayerLauncher), so the
     * screen is already unpaired by then. MainActivity has to be exported -- it is the launcher
     * -- so any other app can send the same extra, and honouring it for a paired screen let one
     * app unpair every TV it could reach.
     */
    fun honourForcedSignIn(requested: Boolean, isPaired: Boolean): Boolean = requested && !isPaired

    fun resolveLocal(
        isPaired: Boolean,
        hasLocalPlaylist: Boolean
    ): LaunchState = if (isPaired || hasLocalPlaylist) {
        LaunchState.Playing()
    } else {
        // Sign-in is the default for an unclaimed device; the pairing code is opt-in from
        // there, so a boot no longer mints a code nobody is looking at.
        LaunchState.SignIn()
    }

    suspend fun refresh(
        currentState: LaunchState,
        register: suspend () -> RegistrationSnapshot
    ): LaunchState {
        return try {
            val registration = register()
            when {
                currentState is LaunchState.Playing -> LaunchState.Playing(registration.screenName)
                registration.status != WAITING_PAIRING -> LaunchState.Playing(registration.screenName)
                else -> LaunchState.Pairing(pairCode = registration.pairCode)
            }
        } catch (_: Exception) {
            if (currentState is LaunchState.Playing) {
                currentState
            } else {
                LaunchState.Pairing(
                    connectionMessage = "No connection. Pairing will resume automatically."
                )
            }
        }
    }

    const val WAITING_PAIRING = "waiting_pairing"
}
