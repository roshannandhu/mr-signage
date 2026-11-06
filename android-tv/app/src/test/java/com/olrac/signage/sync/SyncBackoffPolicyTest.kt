package com.olrac.signage.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class SyncBackoffPolicyTest {
    @Test
    fun serverIntervalIsBounded() {
        assertEquals(60, SyncBackoffPolicy.serverIntervalSeconds(null))
        assertEquals(10, SyncBackoffPolicy.serverIntervalSeconds(1))
        assertEquals(3_600, SyncBackoffPolicy.serverIntervalSeconds(10_000))
    }

    @Test
    fun repeatedFailuresBackOffExponentiallyAndCap() {
        assertEquals(5, SyncBackoffPolicy.failureDelaySeconds(0))
        assertEquals(10, SyncBackoffPolicy.failureDelaySeconds(1))
        assertEquals(40, SyncBackoffPolicy.failureDelaySeconds(3))
        assertEquals(300, SyncBackoffPolicy.failureDelaySeconds(20))
    }
}
