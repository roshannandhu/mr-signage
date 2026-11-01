package com.olrac.signage.data

import android.content.Context

/**
 * How many wrong PINs in a row before entry pauses, and for how long.
 *
 * Persisted across prompt reveals (not scoped to one composition the way the gesture's own
 * window is), so repeating the reveal gesture is not a way around the limit -- five wrong
 * guesses is five wrong guesses, whether they came from one open prompt or five.
 */
object PinAttemptLimiter {
    const val MAX_ATTEMPTS = 5
    const val COOLDOWN_MS = 30_000L

    data class State(val failedCount: Int = 0, val cooldownUntil: Long = 0L)

    /** Milliseconds remaining before entry is allowed again; 0 when not in cooldown. */
    fun cooldownRemainingMs(state: State, nowMs: Long): Long =
        (state.cooldownUntil - nowMs).coerceAtLeast(0L)

    /** The state after one wrong PIN. */
    fun recordFailure(state: State, nowMs: Long): State {
        val count = state.failedCount + 1
        return if (count >= MAX_ATTEMPTS) State(0, nowMs + COOLDOWN_MS) else State(count, state.cooldownUntil)
    }

    /** The state after a correct PIN -- always clears both counters. */
    fun recordSuccess(): State = State()

    private const val PREFS = "signage_prefs"
    private const val KEY_FAILED_COUNT = "pin_failed_count"
    private const val KEY_COOLDOWN_UNTIL = "pin_cooldown_until"

    fun load(context: Context): State {
        val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return State(
            failedCount = prefs.getInt(KEY_FAILED_COUNT, 0),
            cooldownUntil = prefs.getLong(KEY_COOLDOWN_UNTIL, 0L),
        )
    }

    fun save(context: Context, state: State) {
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putInt(KEY_FAILED_COUNT, state.failedCount)
            .putLong(KEY_COOLDOWN_UNTIL, state.cooldownUntil)
            .apply()
    }
}
