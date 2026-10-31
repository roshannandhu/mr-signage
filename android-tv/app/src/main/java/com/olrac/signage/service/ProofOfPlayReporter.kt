package com.olrac.signage.service

import android.content.Context
import android.util.Log
import com.olrac.signage.data.AppDatabase
import com.olrac.signage.network.ApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicBoolean

object ProofOfPlayReporter {
    private const val TAG = "ProofOfPlayReporter"
    private val isFlushing = AtomicBoolean(false)

    suspend fun flush(context: Context): Int = withContext(Dispatchers.IO) {
        if (!isFlushing.compareAndSet(false, true)) {
            return@withContext 0
        }

        try {
            val appContext = context.applicationContext
            val prefs = appContext.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
            // Same reason as PlaylistSynchronizer: the raw preference is not written until
            // DeviceState.deviceId is first read, so this silently reported zero plays on a
            // device that had booted straight into the service.
            val deviceId = com.olrac.signage.data.DeviceState(context).deviceId
            val screenId = prefs.getInt("screen_id", -1)
            val orgId = prefs.getInt("organization_id", -1)
            val database = AppDatabase.getDatabase(appContext)
            val playEventDao = database.playEventDao()
            val clock = com.olrac.signage.data.SignageClock.getInstance(appContext)
            val currentOffsetMs = if (clock.isClockVerified()) {
                clock.currentEstimatedUtcMillis() - System.currentTimeMillis()
            } else {
                prefs.getLong("server_time_offset_ms", 0L)
            }

            var totalUploaded = 0
            // Shrinks only to isolate a batch the server permanently refuses, then resets.
            var batchSize = ProofOfPlayWorker.BATCH_SIZE
            while (true) {
                val asked = batchSize
                val pendingEvents = playEventDao.getPendingEvents(asked)
                if (pendingEvents.isEmpty()) break

                // One shared mapping with ProofOfPlayWorker. This used to be a second copy
                // that had drifted: it passed a `clock_offset_ms` argument that exists on
                // neither the DTO nor the server schema (so the app did not compile), and
                // it never applied the clock correction, meaning events queued before the
                // device first reached the server were uploaded with an uncorrected clock.
                val dtos = pendingEvents.map { it.toDto(currentOffsetMs) }

                val request = PlayLogBatchRequest(
                    device_id = deviceId,
                    screen_id = screenId.takeIf { it > 0 },
                    organization_id = orgId.takeIf { it > 0 },
                    events = dtos
                )

                val response = try {
                    ApiClient.service(appContext).uploadPlayLogs(request)
                } catch (e: Exception) {
                    Log.w(TAG, "Direct play log upload failed: ${e.message}")
                    break
                }

                when (PlayLogUploadPolicy.verdictFor(response.code(), pendingEvents.size)) {
                    PlayLogUploadPolicy.Verdict.ACCEPTED -> {
                        playEventDao.deleteEvents(pendingEvents.map { it.eventId })
                        totalUploaded += pendingEvents.size
                        batchSize = ProofOfPlayWorker.BATCH_SIZE
                    }
                    // These events are billing evidence and the server holds no copy, so a
                    // refusal must cost the single row responsible, not the whole batch.
                    // Halve until the offender is alone, drop exactly it, and let everything
                    // behind it through.
                    PlayLogUploadPolicy.Verdict.NARROW -> {
                        batchSize = PlayLogUploadPolicy.narrowedBatchSize(pendingEvents.size)
                        continue
                    }
                    PlayLogUploadPolicy.Verdict.DROP_SINGLE -> {
                        val rejected = pendingEvents.first()
                        Log.e(TAG, "Dropping play event ${rejected.eventId}: server refused it with ${response.code()}")
                        playEventDao.deleteEvents(listOf(rejected.eventId))
                        batchSize = ProofOfPlayWorker.BATCH_SIZE
                    }
                    PlayLogUploadPolicy.Verdict.RETRY_LATER -> {
                        Log.w(TAG, "Server responded with status code: ${response.code()}")
                        break
                    }
                }

                // Against the size that was ASKED for. Comparing with the already-reset batch
                // size ended the flush after the first narrowed batch.
                if (pendingEvents.size < asked) break
            }

            if (totalUploaded > 0) {
                Log.i(TAG, "Flushed $totalUploaded play events to server")
            }
            totalUploaded
        } catch (e: Exception) {
            Log.w(TAG, "Unexpected error flushing play events", e)
            0
        } finally {
            isFlushing.set(false)
        }
    }
}
