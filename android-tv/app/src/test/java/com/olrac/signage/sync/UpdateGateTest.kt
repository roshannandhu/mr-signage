package com.olrac.signage.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateGateTest {

    private val validDigest = "a".repeat(64)

    @Test
    fun `accepts an https url with a well-formed digest`() {
        assertNull(UpdateGate.rejectionFor("https://cdn.example.com/mr.apk", validDigest))
    }

    @Test
    fun `refuses a release with no digest`() {
        // The regression that mattered: this case used to skip verification and install.
        assertEquals(
            "release has no sha256 digest",
            UpdateGate.rejectionFor("https://cdn.example.com/mr.apk", null)
        )
        assertEquals(
            "release has no sha256 digest",
            UpdateGate.rejectionFor("https://cdn.example.com/mr.apk", "   ")
        )
    }

    @Test
    fun `refuses a malformed digest`() {
        assertEquals(
            "sha256 is not a 64-character hex digest",
            UpdateGate.rejectionFor("https://cdn.example.com/mr.apk", "not-a-digest")
        )
        assertEquals(
            "sha256 is not a 64-character hex digest",
            UpdateGate.rejectionFor("https://cdn.example.com/mr.apk", "a".repeat(63))
        )
    }

    @Test
    fun `refuses cleartext http`() {
        assertEquals(
            "apk url is not https",
            UpdateGate.rejectionFor("http://cdn.example.com/mr.apk", validDigest)
        )
    }

    @Test
    fun `refuses a missing url`() {
        assertEquals("no apk url", UpdateGate.rejectionFor(null, validDigest))
        assertEquals("no apk url", UpdateGate.rejectionFor("", validDigest))
    }

    @Test
    fun `digestMatches is case insensitive`() {
        assertTrue(UpdateGate.digestMatches("AbCd".repeat(16), "aBcD".repeat(16)))
    }

    @Test
    fun `digestMatches refuses when the expectation is absent`() {
        // Absence of a digest is a reason to refuse, never a reason to trust.
        assertFalse(UpdateGate.digestMatches(null, validDigest))
        assertFalse(UpdateGate.digestMatches("", validDigest))
        assertFalse(UpdateGate.digestMatches("short", "short"))
    }

    @Test
    fun `digestMatches refuses a mismatch`() {
        assertFalse(UpdateGate.digestMatches(validDigest, "b".repeat(64)))
    }
}
