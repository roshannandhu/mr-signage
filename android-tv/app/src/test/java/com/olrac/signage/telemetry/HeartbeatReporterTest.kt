package com.olrac.signage.telemetry

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HeartbeatReporterTest {

    @Test
    fun `only a 404 means the server removed this screen`() {
        assertTrue(HeartbeatReporter.signsOutOn(404))
    }

    @Test
    fun `an auth failure never signs the screen out`() {
        // Rotating the server's SECRET_KEY makes every device token fail with a 401 at once.
        // Wiping the pairing, the playlist and every downloaded file in response would have
        // factory-reset the whole fleet.
        assertFalse(HeartbeatReporter.signsOutOn(401))
        assertFalse(HeartbeatReporter.signsOutOn(403))
    }

    @Test
    fun `outages and ordinary answers never sign the screen out`() {
        for (code in listOf(200, 204, 400, 408, 429, 500, 502, 503)) {
            assertFalse("code $code", HeartbeatReporter.signsOutOn(code))
        }
    }
}
