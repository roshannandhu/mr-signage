package com.olrac.signage.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities

class ConnectivityWatcher(
    context: Context,
    private val onAvailable: () -> Unit
) {
    private val connectivityManager =
        context.getSystemService(ConnectivityManager::class.java)
    private var registered = false
    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            val capabilities = connectivityManager.getNetworkCapabilities(network)
            if (capabilities?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true) {
                onAvailable()
            }
        }
    }

    fun start() {
        if (registered) return
        connectivityManager.registerDefaultNetworkCallback(callback)
        registered = true
    }

    fun stop() {
        if (!registered) return
        runCatching { connectivityManager.unregisterNetworkCallback(callback) }
        registered = false
    }

    companion object {
        /** A point-in-time check, for stamping a play as online/offline the moment it
         * finishes -- the long-lived watcher above is for a different question ("tell me
         * when the network comes BACK"), not "was it up just now". */
        fun isOnline(context: Context): Boolean {
            val manager = context.getSystemService(ConnectivityManager::class.java) ?: return false
            val network = manager.activeNetwork ?: return false
            val capabilities = manager.getNetworkCapabilities(network) ?: return false
            return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        }
    }
}
