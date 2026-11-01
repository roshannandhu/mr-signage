package com.olrac.signage.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PinAttemptLimiterTest {

    @Test
    fun `four wrong attempts do not trigger a cooldown`() {
        var state = PinAttemptLimiter.State()
        repeat(4) { state = PinAttemptLimiter.recordFailure(state, nowMs = 1_000L) }
        assertEquals(4, state.failedCount)
        assertEquals(0L, PinAttemptLimiter.cooldownRemainingMs(state, nowMs = 1_000L))
    }

    @Test
    fun `the fifth wrong attempt starts a 30 second cooldown and resets the count`() {
        var state = PinAttemptLimiter.State()
        repeat(5) { state = PinAttemptLimiter.recordFailure(state, nowMs = 1_000L) }
        assertEquals(0, state.failedCount)
        assertEquals(PinAttemptLimiter.COOLDOWN_MS, PinAttemptLimiter.cooldownRemainingMs(state, nowMs = 1_000L))
    }

    @Test
    fun `the cooldown counts down and expires`() {
        var state = PinAttemptLimiter.State()
        repeat(5) { state = PinAttemptLimiter.recordFailure(state, nowMs = 1_000L) }
        assertEquals(20_000L, PinAttemptLimiter.cooldownRemainingMs(state, nowMs = 1_000L + 10_000L))
        assertEquals(0L, PinAttemptLimiter.cooldownRemainingMs(state, nowMs = 1_000L + PinAttemptLimiter.COOLDOWN_MS))
    }

    @Test
    fun `repeating the reveal gesture does not reset the count -- five wrong is five wrong`() {
        // The whole point of persisting this rather than using Compose's own remember: a
        // dismissed-and-reopened prompt must not hand out five fresh guesses.
        var state = PinAttemptLimiter.State()
        repeat(3) { state = PinAttemptLimiter.recordFailure(state, nowMs = 1_000L) }
        // Simulate the prompt being dismissed and the gesture repeated -- a fresh
        // PinPromptScreen composition would reload this same persisted state.
        val reloaded = state
        state = PinAttemptLimiter.recordFailure(reloaded, nowMs = 2_000L)
        state = PinAttemptLimiter.recordFailure(state, nowMs = 2_000L)
        assertTrue("the fifth failure across two sessions should trigger the cooldown",
            PinAttemptLimiter.cooldownRemainingMs(state, nowMs = 2_000L) > 0)
    }

    @Test
    fun `a correct pin clears both the count and any cooldown`() {
        val state = PinAttemptLimiter.recordSuccess()
        assertEquals(0, state.failedCount)
        assertEquals(0L, state.cooldownUntil)
    }
}
