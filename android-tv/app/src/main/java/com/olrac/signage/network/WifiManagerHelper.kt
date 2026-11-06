package com.olrac.signage.network

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.wifi.ScanResult
import android.net.wifi.WifiConfiguration
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSuggestion
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log

data class WifiNetworkItem(
    val ssid: String,
    val bssid: String,
    val level: Int,
    val signalPercentage: Int,
    val signalBars: Int,
    val isSecured: Boolean,
    val isWpa3: Boolean,
    val isConnected: Boolean,
)

class WifiManagerHelper(private val context: Context) {
    private val TAG = "WifiManagerHelper"
    private val appContext = context.applicationContext
    private val wifiManager = appContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    private val mainHandler = Handler(Looper.getMainLooper())

    private var scanReceiver: BroadcastReceiver? = null
    private var isScanReceiverRegistered = false

    // Scan throttle: Android 9+ allows ~4 scans per 2 min. We enforce 10s cooldown.
    private var lastScanRequestMs = 0L
    private val SCAN_COOLDOWN_MS = 10_000L

    // Scan fallback timer: if SCAN_RESULTS_AVAILABLE_ACTION never fires, show stale data.
    private var scanTimeoutRunnable: Runnable? = null
    private val SCAN_TIMEOUT_MS = 3_000L

    var onNetworksUpdated: ((List<WifiNetworkItem>) -> Unit)? = null
    var onWifiStateChanged: ((Boolean) -> Unit)? = null

    // Reports true if a scan was actually requested (not throttled).
    var onScanThrottled: (() -> Unit)? = null

    val isWifiEnabled: Boolean
        get() = try { wifiManager.isWifiEnabled } catch (e: Exception) { false }

    val isWifiConnected: Boolean
        get() = currentConnectedSsid != null

    val currentSignalBars: Int
        get() = runCatching {
            val info = wifiManager.connectionInfo
            if (info != null && info.networkId != -1)
                WifiManager.calculateSignalLevel(info.rssi, 4)
            else 0
        }.getOrDefault(0)

    val currentConnectedSsid: String?
        get() = runCatching {
            val raw = wifiManager.connectionInfo?.ssid?.removeSurrounding("\"")
            if (raw.isNullOrBlank() || raw == "<unknown ssid>") null else raw
        }.getOrNull()

    // ─────────────────────────────────────────────────────────────────────────
    // Wi-Fi toggle
    // ─────────────────────────────────────────────────────────────────────────

    fun setWifiEnabled(enabled: Boolean): Boolean {
        return try {
            @Suppress("DEPRECATION")
            val success = wifiManager.setWifiEnabled(enabled)
            onWifiStateChanged?.invoke(enabled)
            if (enabled) {
                mainHandler.postDelayed({ startScan() }, 1_200L)
            }
            success
        } catch (e: Exception) {
            Log.w(TAG, "Failed to toggle wifi", e)
            false
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Receivers
    // ─────────────────────────────────────────────────────────────────────────

    fun registerReceivers() {
        if (isScanReceiverRegistered) return
        val filter = IntentFilter().apply {
            addAction(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION)
            addAction(WifiManager.WIFI_STATE_CHANGED_ACTION)
            addAction(WifiManager.NETWORK_STATE_CHANGED_ACTION)
        }
        scanReceiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, intent: Intent?) {
                when (intent?.action) {
                    WifiManager.SCAN_RESULTS_AVAILABLE_ACTION -> {
                        cancelScanTimeout()
                        updateNetworksFromScan()
                    }
                    WifiManager.WIFI_STATE_CHANGED_ACTION -> {
                        val state = intent.getIntExtra(WifiManager.EXTRA_WIFI_STATE, WifiManager.WIFI_STATE_UNKNOWN)
                        val enabled = (state == WifiManager.WIFI_STATE_ENABLED)
                        onWifiStateChanged?.invoke(enabled)
                        if (enabled) startScan()
                    }
                    WifiManager.NETWORK_STATE_CHANGED_ACTION -> {
                        updateNetworksFromScan()
                    }
                }
            }
        }
        try {
            appContext.registerReceiver(scanReceiver, filter)
            isScanReceiverRegistered = true
        } catch (e: Exception) {
            Log.w(TAG, "Could not register scan receiver", e)
        }
    }

    fun unregisterReceivers() {
        if (!isScanReceiverRegistered) return
        cancelScanTimeout()
        scanReceiver?.let {
            try { appContext.unregisterReceiver(it) } catch (_: Exception) {}
        }
        scanReceiver = null
        isScanReceiverRegistered = false
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Scan — throttle-aware with 3s fallback
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * Returns true if a fresh scan was actually started.
     * Returns false if Android throttle is active (cooldown < 10s) — caller should
     * show stale data and inform the user via [onScanThrottled].
     */
    fun startScan(): Boolean {
        if (!isWifiEnabled) {
            setWifiEnabled(true)
            return false
        }

        val now = System.currentTimeMillis()
        val sinceLastScan = now - lastScanRequestMs

        // Always serve stale cache immediately so the list is never empty.
        updateNetworksFromScan()

        if (sinceLastScan < SCAN_COOLDOWN_MS && lastScanRequestMs != 0L) {
            // Android will throttle this — notify caller.
            onScanThrottled?.invoke()
            return false
        }

        lastScanRequestMs = now

        try {
            @Suppress("DEPRECATION")
            val scanStarted = wifiManager.startScan()
            Log.i(TAG, "wifiManager.startScan() returned: $scanStarted")
        } catch (e: Exception) {
            Log.d(TAG, "startScan exception: ${e.message}")
        }

        // If SCAN_RESULTS_AVAILABLE_ACTION never fires within 3s, fall back to stale cache.
        scheduleScanTimeout()
        return true
    }

    private fun scheduleScanTimeout() {
        cancelScanTimeout()
        val r = Runnable {
            Log.d(TAG, "Scan timeout — returning stale cache")
            updateNetworksFromScan()
        }
        scanTimeoutRunnable = r
        mainHandler.postDelayed(r, SCAN_TIMEOUT_MS)
    }

    private fun cancelScanTimeout() {
        scanTimeoutRunnable?.let { mainHandler.removeCallbacks(it) }
        scanTimeoutRunnable = null
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Parse scan results
    // ─────────────────────────────────────────────────────────────────────────

    fun updateNetworksFromScan() {
        try {
            val results: List<ScanResult> = wifiManager.scanResults ?: emptyList()
            val currentSsid = currentConnectedSsid
            Log.i(TAG, "updateNetworksFromScan: raw scanResults size=${results.size}, currentSsid=$currentSsid")
            val networkMap = mutableMapOf<String, WifiNetworkItem>()

            for (sr in results) {
                val ssid = sr.SSID
                if (ssid.isNullOrBlank()) continue

                val level = sr.level
                val percentage = WifiManager.calculateSignalLevel(level, 100)
                val bars = WifiManager.calculateSignalLevel(level, 4).coerceIn(0, 4)
                val caps = sr.capabilities
                val isWpa3 = caps.contains("SAE") || caps.contains("WPA3")
                val isSecured = isWpa3 || caps.contains("WPA") || caps.contains("WEP") || caps.contains("PSK") || caps.contains("EAP")
                val isConnected = (ssid == currentSsid)

                val existing = networkMap[ssid]
                if (existing == null || sr.level > existing.level) {
                    networkMap[ssid] = WifiNetworkItem(
                        ssid = ssid,
                        bssid = sr.BSSID,
                        level = level,
                        signalPercentage = percentage,
                        signalBars = bars,
                        isSecured = isSecured,
                        isWpa3 = isWpa3,
                        isConnected = isConnected,
                    )
                }
            }

            val sorted = networkMap.values.sortedWith(
                compareByDescending<WifiNetworkItem> { it.isConnected }
                    .thenByDescending { it.level }
            )
            onNetworksUpdated?.invoke(sorted)
        } catch (e: Exception) {
            Log.w(TAG, "Error processing scan results", e)
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Connect — Device Owner path (direct + suggestion, WPA2/WPA3 aware)
    // ─────────────────────────────────────────────────────────────────────────

    fun connectToNetwork(
        ssid: String,
        password: String?,
        isWpa3: Boolean = false,
        onResult: (success: Boolean, message: String) -> Unit
    ) {
        var callbackTriggered = false
        val cleanSsid = ssid.removeSurrounding("\"")

        val finishWith: (Boolean, String) -> Unit = { success, msg ->
            if (!callbackTriggered) {
                callbackTriggered = true
                mainHandler.post {
                    updateNetworksFromScan()
                    onResult(success, msg)
                }
            }
        }

        try {
            // ── Path 1: WifiNetworkSuggestion (Android 10+, honours WPA3) ──────
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val builder = WifiNetworkSuggestion.Builder()
                    .setSsid(cleanSsid)
                    .setIsAppInteractionRequired(false)
                    .setIsUserInteractionRequired(false)
                if (!password.isNullOrBlank()) {
                    if (isWpa3 && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        builder.setWpa3Passphrase(password)
                    } else {
                        builder.setWpa2Passphrase(password)
                    }
                }
                runCatching {
                    wifiManager.removeNetworkSuggestions(emptyList())
                    val res = wifiManager.addNetworkSuggestions(listOf(builder.build()))
                    Log.i(TAG, "addNetworkSuggestions result=$res for $cleanSsid")
                }
            }

            // ── Path 2: WifiConfiguration (deprecated but works for Device Owner) ─
            @Suppress("DEPRECATION")
            val conf = WifiConfiguration().apply {
                SSID = "\"$cleanSsid\""
                if (password.isNullOrBlank()) {
                    allowedKeyManagement.set(WifiConfiguration.KeyMgmt.NONE)
                } else {
                    preSharedKey = "\"$password\""
                }
            }
            @Suppress("DEPRECATION")
            val netId = wifiManager.addNetwork(conf)
            Log.i(TAG, "addNetwork netId=$netId for $cleanSsid")
            if (netId != -1) {
                @Suppress("DEPRECATION")
                wifiManager.disconnect()
                @Suppress("DEPRECATION")
                wifiManager.enableNetwork(netId, true)
                @Suppress("DEPRECATION")
                wifiManager.reconnect()
            }

            // ── Polling: 300ms intervals, 8s max ─────────────────────────────────
            var pollCount = 0
            val pollRunnable = object : Runnable {
                override fun run() {
                    val current = currentConnectedSsid
                    when {
                        current != null && current.equals(cleanSsid, ignoreCase = true) -> {
                            finishWith(true, "Connected to $cleanSsid ✓")
                        }
                        pollCount >= 26 -> {
                            // 26 × 300ms = ~8s
                            if (currentConnectedSsid?.equals(cleanSsid, ignoreCase = true) == true) {
                                finishWith(true, "Connected to $cleanSsid ✓")
                            } else {
                                finishWith(false, "Could not connect. Check the password and try again.")
                            }
                        }
                        else -> {
                            pollCount++
                            mainHandler.postDelayed(this, 300L)
                        }
                    }
                }
            }
            mainHandler.postDelayed(pollRunnable, 300L)

        } catch (e: Exception) {
            Log.e(TAG, "connectToNetwork error", e)
            finishWith(false, e.localizedMessage ?: "Connection error")
        }
    }

    // Returns remaining cooldown ms (0 if free to scan).
    fun scanCooldownRemainingMs(): Long {
        val since = System.currentTimeMillis() - lastScanRequestMs
        return if (lastScanRequestMs == 0L) 0L else (SCAN_COOLDOWN_MS - since).coerceAtLeast(0L)
    }
}
