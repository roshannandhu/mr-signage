package com.olrac.signage.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "play_events")
data class PlayEventEntity(
    @PrimaryKey
    val eventId: String,
    val mediaId: Int?,
    val playlistId: Int?,
    val campaignId: Int?,
    val deviceStartedAt: String,
    val deviceFinishedAt: String,
    val correctedStartedAt: String,
    val correctedFinishedAt: String,
    val durationMs: Int,
    val status: String,
    val errorMessage: String? = null,
    /**
     * Server-clock offset known when this play was recorded, or null if the device had
     * never successfully reached the server.
     *
     * The corrected timestamps above are computed with whatever offset was available at
     * record time. On a TV that has not synced yet that offset is 0, so `corrected` equals
     * a device clock that may be hours wrong -- and because rollups bucket on
     * `corrected_started_at`, a week of offline plays landed in the wrong hours and stayed
     * there. Nothing ever revisited them.
     *
     * Keeping the offset that was actually in force lets the upload distinguish the two
     * cases: an event stamped with a known offset is already right and is left alone, and
     * one stamped with no offset is corrected with the offset learned since. See
     * ProofOfPlayWorker.
     */
    val clockOffsetMs: Long? = null,
    /**
     * Whether the TV had a live network connection the moment this play finished.
     *
     * Nullable for the same reason clockOffsetMs is: a row recorded before this column
     * existed has no honest answer, and null says that rather than guessing. The upload
     * side (ProofOfPlayWorker) estimates one for those from the gap between when the play
     * finished and when it was finally uploaded, exactly as the server does for an app too
     * old to send this at all.
     */
    val playedOffline: Boolean? = null
)
