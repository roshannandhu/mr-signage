package com.olrac.signage.service

/**
 * How to treat a server response to a batch of play events.
 *
 * Play events are billing evidence and the server keeps no copy, so "the upload failed" must
 * never be answered by deleting them. The one case that genuinely cannot be retried is a
 * batch the server will refuse forever -- and the old code answered that by dropping the
 * whole batch, destroying up to BATCH_SIZE good events because of one bad row. Callers
 * narrow the batch down instead and drop only the single event actually refused.
 */
object PlayLogUploadPolicy {
    /** 4xx codes that are worth retrying rather than a verdict on the payload. */
    private val RETRYABLE = setOf(401, 403, 408, 429)

    fun isPermanentRejection(code: Int): Boolean = code in 400..499 && code !in RETRYABLE

    /** Events to send next after a permanent rejection of [batchSize]; 1 means "isolated". */
    fun narrowedBatchSize(batchSize: Int): Int = maxOf(1, batchSize / 2)

    /** What to do with a batch after the server answered. */
    enum class Verdict {
        /** Stored. Delete the batch and carry on. */
        ACCEPTED,

        /** Nothing is wrong with the events: keep them all and try again later. */
        RETRY_LATER,

        /** Refused for good, and more than one event: halve the batch to find the offender. */
        NARROW,

        /** Refused for good, and it is down to one event: that event alone is dropped. */
        DROP_SINGLE,
    }

    /**
     * The one answer both upload paths act on. ProofOfPlayWorker used to delete the whole
     * batch (up to 500 plays) on any permanent refusal -- a 404 for a removed screen, or a 422
     * after a schema change, would discard every queued play -- while ProofOfPlayReporter had
     * already learned to narrow. Two copies of the rule is how they came to disagree.
     */
    fun verdictFor(code: Int, sent: Int): Verdict = when {
        code in 200..299 -> Verdict.ACCEPTED
        isPermanentRejection(code) -> if (sent > 1) Verdict.NARROW else Verdict.DROP_SINGLE
        else -> Verdict.RETRY_LATER
    }
}
