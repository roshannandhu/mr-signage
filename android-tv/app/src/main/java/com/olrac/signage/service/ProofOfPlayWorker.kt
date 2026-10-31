package com.olrac.signage.service

import android.content.Context
import android.os.SystemClock
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.Constraints
import androidx.work.NetworkType
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.olrac.signage.data.AppDatabase
import com.olrac.signage.data.PlayEventEntity
import com.olrac.signage.network.ApiClient
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

data class PlayEventDto(
    val event_id: String,
    val media_id: Int?,
    val playlist_id: Int?,
    val campaign_id: Int?,
    val device_started_at: String,
    val device_finished_at: String,
    val corrected_started_at: String,
    val corrected_finished_at: String,
    val duration_ms: Int,
    val status: String,
    val error_message: String?
)

data class PlayLogBatchRequest(
    // The device id identifies the player; screen_id and organization_id come back from
    // the heartbeat/sync and are verified server side against the device's authenticated screen.
    val device_id: String,
    val screen_id: Int?,
    val organization_id: Int?,
    val events: List<PlayEventDto>
)

// Internal rather than private: ProofOfPlayReporter drains the same table on the same
// contract, and its own copy of this mapping had already drifted -- it passed a
// `clock_offset_ms` argument that exists on neither the DTO nor the server schema, so
// the app did not compile, and it skipped the offset correction below entirely.
internal fun PlayEventEntity.toDto(currentOffsetMs: Long): PlayEventDto {
    // An event carrying no offset was stamped before this device had ever reached the
    // server, so its "corrected" times are a device clock that may be hours out. Redo
    // the correction with the offset since learned. An event that recorded its own
    // offset was already stamped against a known-good clock and is left untouched --
    // re-correcting it with a newer offset would move a timestamp that was right.
    if (clockOffsetMs != null) {
        return PlayEventDto(
            event_id = eventId,
            media_id = mediaId,
            playlist_id = playlistId,
            campaign_id = campaignId,
            device_started_at = deviceStartedAt,
            device_finished_at = deviceFinishedAt,
            corrected_started_at = correctedStartedAt,
            corrected_finished_at = correctedFinishedAt,
            duration_ms = durationMs,
            status = status,
            error_message = errorMessage
        )
    }
    return PlayEventDto(
        event_id = eventId,
        media_id = mediaId,
        playlist_id = playlistId,
        campaign_id = campaignId,
        device_started_at = deviceStartedAt,
        device_finished_at = deviceFinishedAt,
        corrected_started_at = shift(deviceStartedAt, currentOffsetMs),
        corrected_finished_at = shift(deviceFinishedAt, currentOffsetMs),
        duration_ms = durationMs,
        status = status,
        error_message = errorMessage
    )
}

private fun isoFormatter() =
    SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }


internal fun shift(timestamp: String, offsetMs: Long): String {
    val parsed = runCatching { isoFormatter().parse(timestamp) }.getOrNull() ?: return timestamp
    return isoFormatter().format(Date(parsed.time + offsetMs))
}


class ProofOfPlayWorker(
    appContext: Context,
    workerParams: WorkerParameters
) : CoroutineWorker(appContext, workerParams) {

    override suspend fun doWork(): Result {
        val prefs = applicationContext.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
        // Result.failure() is terminal: WorkManager never retries that request again, so a
        // single early run on a fresh install permanently disabled proof-of-play upload for
        // the life of the install. Reading through DeviceState means the id is always
        // available; retry() is the correct answer for anything transient.
        val deviceId = com.olrac.signage.data.DeviceState(applicationContext).deviceId
        val screenId = prefs.getInt("screen_id", -1)
        val orgId = prefs.getInt("organization_id", -1)

        val database = AppDatabase.getDatabase(applicationContext)
        val playEventDao = database.playEventDao()

        // Offset learned by SignageClock or heartbeat. Applied below to any event that was
        // recorded before this device had ever reached the server.
        val clock = com.olrac.signage.data.SignageClock.getInstance(applicationContext)
        val currentOffsetMs = if (clock.isClockVerified()) {
            clock.currentEstimatedUtcMillis() - System.currentTimeMillis()
        } else {
            prefs.getLong("server_time_offset_ms", 0L)
        }

        // Drain in a loop rather than uploading one batch and going back to sleep.
        //
        // The worker is periodic on a 15-minute cadence -- WorkManager's floor -- and used
        // to send a single batch of BATCH_SIZE per run. That capped the fleet's reporting
        // rate at 2,000 events an hour per screen, no matter how much was queued. A screen
        // off the network for a month accumulates hundreds of thousands of plays, so it
        // would still have been dribbling out a fortnight later, and "did my advert run
        // today?" was unanswerable for days after any real outage.
        //
        // Bounded by wall-clock budget, not by batch count: WorkManager stops a worker at
        // around ten minutes, and being killed mid-drain is harmless (nothing is deleted
        // until the server acknowledges it) but wastes the round trips already made.
        val startedAt = SystemClock.elapsedRealtime()
        var uploaded = 0
        // Shrinks only to isolate a batch the server permanently refuses, then resets.
        var batchSize = BATCH_SIZE
        while (true) {
            val asked = batchSize
            val pendingEvents = playEventDao.getPendingEvents(asked)
            if (pendingEvents.isEmpty()) break

            val dtos = pendingEvents.map { it.toDto(currentOffsetMs) }
            val request = PlayLogBatchRequest(
                device_id = deviceId,
                screen_id = screenId.takeIf { it > 0 },
                organization_id = orgId.takeIf { it > 0 },
                events = dtos
            )

            val verdict = try {
                val response = ApiClient.service(applicationContext).uploadPlayLogs(request)
                PlayLogUploadPolicy.verdictFor(response.code(), pendingEvents.size)
            } catch (e: Exception) {
                Log.w(TAG, "Play log upload failed", e)
                PlayLogUploadPolicy.Verdict.RETRY_LATER
            }

            when (verdict) {
                PlayLogUploadPolicy.Verdict.RETRY_LATER ->
                    return if (uploaded > 0) Result.success() else Result.retry()
                PlayLogUploadPolicy.Verdict.ACCEPTED -> {
                    playEventDao.deleteEvents(pendingEvents.map { it.eventId })
                    uploaded += pendingEvents.size
                    batchSize = BATCH_SIZE
                }
                // These events are billing evidence and the server holds no copy, so a refusal
                // must cost the single row responsible, not the whole batch. Halve until the
                // offender is alone, drop exactly it, and let everything behind it through.
                PlayLogUploadPolicy.Verdict.NARROW -> {
                    batchSize = PlayLogUploadPolicy.narrowedBatchSize(pendingEvents.size)
                    continue
                }
                PlayLogUploadPolicy.Verdict.DROP_SINGLE -> {
                    val rejected = pendingEvents.first()
                    Log.e(TAG, "Dropping play event ${rejected.eventId}: the server refused it permanently")
                    playEventDao.deleteEvents(listOf(rejected.eventId))
                    batchSize = BATCH_SIZE
                }
            }

            // A short batch means the queue is empty; nothing left to drain. Compared with the
            // size that was ASKED for, not the reset one, or a narrowed batch would end the drain.
            if (pendingEvents.size < asked) break

            if (SystemClock.elapsedRealtime() - startedAt > DRAIN_BUDGET_MS) {
                // Still behind. Chain a fresh run immediately instead of waiting out the
                // periodic interval, so a large backlog clears in minutes rather than days.
                Log.i(TAG, "Drain budget reached after $uploaded events; continuing in a follow-up run")
                enqueueNow(applicationContext)
                break
            }
        }

        if (uploaded > 0) Log.i(TAG, "Uploaded $uploaded play events")
        return Result.success()
    }

    companion object {
        private const val TAG = "ProofOfPlay"

        /** Server rejects anything larger; see routers/screens.py. */
        const val BATCH_SIZE = 500

        /** Leaves headroom inside WorkManager's ~10 minute execution window. */
        const val DRAIN_BUDGET_MS = 4L * 60 * 1000

        const val UNIQUE_WORK_NAME = "proof-of-play-now"

        /**
         * Upload as soon as there is a network, without waiting for the periodic run.
         *
         * Called when connectivity returns and when a large backlog needs more than one
         * drain window. REPLACE rather than KEEP so a queued-but-not-started request is
         * superseded instead of stacking up.
         */
        fun enqueueNow(context: Context) {
            val request = OneTimeWorkRequestBuilder<ProofOfPlayWorker>()
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            // WorkManager.getInstance throws if it is not initialised, and this is called from
            // the player's play-end path: an exception here would take the app down at the end
            // of the first advert. A missed enqueue is harmless -- the periodic run still drains.
            try {
                WorkManager.getInstance(context)
                    .enqueueUniqueWork(UNIQUE_WORK_NAME, ExistingWorkPolicy.REPLACE, request)
            } catch (e: Exception) {
                Log.w(TAG, "Could not schedule a play log upload", e)
            }
        }

    }
}
