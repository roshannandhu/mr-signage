package com.olrac.signage.service

import com.olrac.signage.service.PlayLogUploadPolicy.Verdict
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Play events are billing evidence and the server keeps no copy, so what we do with a batch
 * after an error decides whether an advertiser can be billed for plays that really happened.
 * ProofOfPlayWorker used to delete the whole batch (up to 500 plays) on any permanent refusal.
 */
class PlayLogUploadPolicyTest {

    @Test
    fun `a stored batch is accepted`() {
        assertEquals(Verdict.ACCEPTED, PlayLogUploadPolicy.verdictFor(200, 500))
        assertEquals(Verdict.ACCEPTED, PlayLogUploadPolicy.verdictFor(201, 1))
    }

    @Test
    fun `a permanent refusal of a large batch narrows it and deletes nothing`() {
        // 404 is a screen the server no longer knows; 422 is a payload it will not parse.
        for (code in listOf(400, 404, 413, 422)) {
            assertEquals("code $code", Verdict.NARROW, PlayLogUploadPolicy.verdictFor(code, 500))
            assertEquals("code $code", Verdict.NARROW, PlayLogUploadPolicy.verdictFor(code, 2))
        }
    }

    @Test
    fun `only a single refused event is ever dropped`() {
        assertEquals(Verdict.DROP_SINGLE, PlayLogUploadPolicy.verdictFor(422, 1))
        assertEquals(Verdict.DROP_SINGLE, PlayLogUploadPolicy.verdictFor(404, 1))
    }

    @Test
    fun `auth and rate limit errors keep every event`() {
        // These clear once the device re-authenticates or the limit passes.
        for (code in listOf(401, 403, 408, 429)) {
            assertEquals("code $code", Verdict.RETRY_LATER, PlayLogUploadPolicy.verdictFor(code, 500))
            assertEquals("code $code", Verdict.RETRY_LATER, PlayLogUploadPolicy.verdictFor(code, 1))
        }
    }

    @Test
    fun `server errors keep every event`() {
        for (code in listOf(500, 502, 503, 504)) {
            assertEquals("code $code", Verdict.RETRY_LATER, PlayLogUploadPolicy.verdictFor(code, 500))
        }
    }

    @Test
    fun `narrowing halves down to one and stops there`() {
        var size = 500
        val seen = mutableListOf<Int>()
        while (size > 1) {
            size = PlayLogUploadPolicy.narrowedBatchSize(size)
            seen.add(size)
        }
        assertEquals(listOf(250, 125, 62, 31, 15, 7, 3, 1), seen)
        assertEquals(1, PlayLogUploadPolicy.narrowedBatchSize(1))
    }
}
