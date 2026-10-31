package com.olrac.signage.data

/**
 * Why a play stopped, and what that means for the record written about it.
 *
 * Completion used to be inferred arithmetically: `durationMs < item.duration * 1000 - 2000`
 * meant "partial". That compared wall-clock time against the *playlist slot* duration,
 * which is not the length of the media. `PlaylistItem.duration` defaults to 10 seconds
 * whenever the content's real length has not been probed, so a five-second advert that
 * played from first frame to last was recorded as partial -- 5000 < 8000. Completion rate
 * is the number advertisers are billed on, so it was wrong in the one place it must not be.
 *
 * The player already knows exactly why it moved on. Recording that directly removes the
 * inference, and with it the whole class of bug: no threshold to tune, and it is equally
 * correct for a 5-second advert and a 5-minute one.
 */
enum class PlayEndReason {
    /** Reached its natural end: STATE_ENDED, or the planned hand-over point for a video. */
    PLAYED_TO_END,

    /** Deliberately cut short -- supervisor skipped a damaged item. */
    SKIPPED,

    /** The player reported an error for this item. */
    FAILED,

    /**
     * Stopped by something other than itself: a re-sync replaced the playlist, or the app
     * died mid-play and this record was recovered on the next launch.
     */
    INTERRUPTED,
}

object PlayCompletion {

    /**
     * Longest play we will believe. A TV whose clock is corrected by NTP mid-advert can
     * produce a duration of days, or a negative one; either overflows the Int the wire
     * format uses and poisons the duration totals on the report.
     */
    const val MAX_PLAUSIBLE_MS = 24L * 60 * 60 * 1000

    /** The wire status for one finished play. */
    fun status(reason: PlayEndReason, error: String?): String = when {
        error != null || reason == PlayEndReason.FAILED -> "error"
        reason == PlayEndReason.PLAYED_TO_END -> "completed"
        // Skipped and interrupted both mean the audience did not see the whole thing.
        // Reporting either as completed would overstate delivery to the advertiser.
        else -> "partial"
    }

    /** Stored beside a play recorded while the clock could not be trusted. Not an error. */
    const val CLOCK_UNVERIFIED_NOTE = "time_unverified_offline"

    /** What gets written for one finished play. */
    data class Recorded(val status: String, val errorMessage: String?)

    /**
     * The status and note for a finished play.
     *
     * The clock note rides alongside the status; it must never decide it. It was passed to
     * [status] as if it were an error, so every play recorded before the device first heard
     * the time -- which includes COMPLETED plays after an offline boot -- was stored as an
     * "error" and never counted towards what the advertiser is billed on.
     */
    fun describe(reason: PlayEndReason, error: String?, clockVerified: Boolean): Recorded =
        Recorded(
            status = status(reason, error),
            errorMessage = if (!clockVerified && error == null) CLOCK_UNVERIFIED_NOTE else error,
        )

    /**
     * Elapsed play time, clamped to something a report can use.
     *
     * A backwards clock jump yields a negative span; that is not evidence of a play, so it
     * clamps to zero rather than being sent as a negative Int.
     */
    fun durationMs(startedAtMs: Long, finishedAtMs: Long): Int =
        (finishedAtMs - startedAtMs).coerceIn(0L, MAX_PLAUSIBLE_MS).toInt()

    /**
     * Whether a measured span is trustworthy enough to keep the reason it claims.
     *
     * Used only to downgrade an implausible "completed": if the clock moved under us the
     * span tells us nothing, and claiming a full play on no evidence is the failure mode
     * that costs credibility in a billing dispute.
     */
    fun isPlausible(startedAtMs: Long, finishedAtMs: Long): Boolean {
        val span = finishedAtMs - startedAtMs
        return span >= 0 && span <= MAX_PLAUSIBLE_MS
    }
}
