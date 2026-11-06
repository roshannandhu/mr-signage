package com.olrac.signage.boot

import android.os.SystemClock

/**
 * One "Open app on TV" press, acted on once.
 *
 * The dashboard delivers that press twice by design: pushed over the screen's socket for speed,
 * and queued for the next heartbeat or sync in case the socket is down. When both arrive, the
 * queued copy lands seconds after the pushed one. On the Lenovo TB-8505F: pushed at 12:50:03,
 * heartbeat copy at 12:50:11. If an operator used those seconds to exit the player, the late
 * copy dragged it straight back over the home screen, and the exit looked broken.
 *
 * So a remote bring-to-front is ignored when another was acted on within [DUPLICATE_WINDOW_MS]:
 * longer than the heartbeat cycle that carries the queued copy, short enough that a genuine
 * second press still works. Boot, crash recovery and the supervisor do not go through here.
 */
object RemoteLaunchGate {
    const val DUPLICATE_WINDOW_MS = 2_500L

    private var lastAllowedAt: Long? = null

    @Synchronized
    fun allow(nowMs: Long = SystemClock.elapsedRealtime()): Boolean {
        val last = lastAllowedAt
        if (last != null && nowMs - last < DUPLICATE_WINDOW_MS) return false
        lastAllowedAt = nowMs
        return true
    }

    @Synchronized
    internal fun resetForTest() {
        lastAllowedAt = null
    }
}
