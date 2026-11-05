package com.olrac.signage.sync

object SyncBackoffPolicy {
    private const val MIN_INTERVAL_SECONDS = 10
    private const val MAX_INTERVAL_SECONDS = 3_600
    private const val INITIAL_FAILURE_DELAY_SECONDS = 5
    private const val MAX_FAILURE_DELAY_SECONDS = 300

    fun serverIntervalSeconds(value: Int?): Int =
        (value ?: 60).coerceIn(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)

    fun failureDelaySeconds(consecutiveFailures: Int): Int {
        val exponent = consecutiveFailures.coerceIn(0, 10)
        return (INITIAL_FAILURE_DELAY_SECONDS * (1 shl exponent))
            .coerceAtMost(MAX_FAILURE_DELAY_SECONDS)
    }
}
