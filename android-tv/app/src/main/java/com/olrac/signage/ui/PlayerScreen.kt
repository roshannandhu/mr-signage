package com.olrac.signage.ui

import android.graphics.Color as AndroidColor
import android.graphics.BitmapFactory
import android.net.Uri
import android.util.Log
import android.view.LayoutInflater
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import com.olrac.signage.R
import com.olrac.signage.data.PlayCompletion
import com.olrac.signage.data.PlayEndReason
import com.olrac.signage.data.PlaylistItemEntity
import com.olrac.signage.data.TransitionSpec
import com.olrac.signage.data.TransitionSpecResolver
import com.olrac.signage.data.TransitionType
import com.olrac.signage.sync.PlaylistSynchronizer
import com.olrac.signage.telemetry.PlaybackTelemetry
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.withContext
import java.io.File
import java.util.WeakHashMap
import kotlin.coroutines.resume

@Composable
fun PlayerScreen(viewModel: PlayerViewModel = viewModel()) {
    val playlist by viewModel.playlist.collectAsState()
    val context = LocalContext.current
    val telemetry = remember(context) { PlaybackTelemetry(context) }

    LaunchedEffect(playlist.isEmpty()) {
        if (playlist.isEmpty()) telemetry.reportIdle()
    }

    if (playlist.isEmpty()) {
        // "Waiting for assigned content" is only true when the server assigned none. During
        // a media outage it is a lie that costs hours: the screen said nothing was booked
        // while four paid campaigns sat on it, so the fault was hunted in the bookings.
        val unreachable = remember(playlist) { PlaylistSynchronizer.unreachableItemCount(context) }
        // The tenant's own brand once this TV has paired -- "MR Signage" belongs on the
        // sign-in screen, which a paired TV never shows again.
        val branding = remember(playlist) { PlaylistSynchronizer.branding(context) }
        val background = remember(branding.color) {
            branding.color?.let { hex -> runCatching { Color(AndroidColor.parseColor(hex)) }.getOrNull() } ?: Color.Black
        }
        Box(
            modifier = Modifier.fillMaxSize().background(background),
            contentAlignment = Alignment.Center
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                if (branding.logoUrl != null) {
                    AsyncImage(
                        model = branding.logoUrl,
                        contentDescription = null,
                        modifier = Modifier.size(72.dp).padding(bottom = 16.dp),
                        contentScale = ContentScale.Fit,
                    )
                }
                Text(
                    text = if (unreachable > 0) {
                        "${branding.name ?: "MR Signage"}\n$unreachable advert${if (unreachable == 1) "" else "s"} assigned " +
                            "but could not be downloaded.\nThe server is not serving media."
                    } else {
                        "${branding.name ?: "MR Signage"}\nWaiting for assigned content"
                    },
                    color = Color.White,
                    textAlign = TextAlign.Center,
                )
            }
        }
        return
    }

    // Re-read on every playlist change rather than held in state: a sync that clears the
    // reason also replaces the playlist, so the notice goes as soon as the workspace is
    // paid up, without the player being restarted.
    val serviceState = remember(playlist) { PlaylistSynchronizer.serviceState(context) }

    Box(modifier = Modifier.fillMaxSize()) {
        DualSurfacePlayer(playlist, telemetry, onReloadPlaylist = { viewModel.reloadPlaylist() })
        serviceState?.let { ServiceNotice(it) }
    }
}

/**
 * Why this screen is showing the demo reel instead of the venue's adverts.
 *
 * Deliberately quiet. This hangs on a wall in someone's shop, usually in front of their
 * customers, so it reads as a caption on the reel rather than an error on a broken
 * television -- no red, no warning triangle, nothing that looks like a fault in the venue.
 * The reel keeps playing underneath; this only explains it.
 */
@Composable
private fun ServiceNotice(state: String) {
    val (headline, detail) = when (state) {
        "expired" -> "Subscription ended" to
            "This display is paused until the plan is renewed."
        "suspended" -> "Display paused" to
            "This workspace has been suspended by the platform operator."
        "rejected" -> "Not activated" to
            "This workspace was not approved."
        // pending_approval, and anything a newer server invents.
        else -> "Setting up" to
            "This display is ready and waiting for its workspace to be activated."
    }

    Box(
        modifier = Modifier.fillMaxSize().padding(40.dp),
        contentAlignment = Alignment.BottomStart
    ) {
        Column(
            modifier = Modifier
                .clip(RoundedCornerShape(14.dp))
                .background(Color(0xCC0B0E14))
                .padding(horizontal = 26.dp, vertical = 20.dp)
        ) {
            Text(
                text = headline,
                color = Color.White,
                fontSize = 30.sp,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Start
            )
            Text(
                text = detail,
                color = Color(0xB3FFFFFF),
                fontSize = 19.sp,
                modifier = Modifier.padding(top = 6.dp)
            )
        }
    }
}

@Composable
private fun DualSurfacePlayer(
    playlist: List<PlaylistItemEntity>,
    telemetry: PlaybackTelemetry,
    onReloadPlaylist: () -> Unit
) {
    val context = LocalContext.current
    val visiblePlaybackBaselines = remember { WeakHashMap<ExoPlayer, DecoderSnapshot>() }
    val players = remember {
        List(2) {
            ExoPlayer.Builder(context).build().apply {
                videoScalingMode = C.VIDEO_SCALING_MODE_SCALE_TO_FIT_WITH_CROPPING
                setAudioAttributes(
                    androidx.media3.common.AudioAttributes.Builder()
                        .setUsage(C.USAGE_MEDIA)
                        .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
                        .build(),
                    /* handleAudioFocus = */ true
                )
            }
        }
    }
    val transitionProgress = remember { Animatable(0f) }
    val transitionMutex = remember { Mutex() }
    var currentIndex by remember { mutableIntStateOf(0) }
    var currentSlot by remember { mutableIntStateOf(0) }
    var playbackEpoch by remember { mutableIntStateOf(0) }
    var currentItemId by remember { mutableStateOf<Int?>(null) }
    var currentItemStartedAtMs by remember { mutableStateOf<Long?>(null) }

    val scope = androidx.compose.runtime.rememberCoroutineScope()
    // advance() must be declared before supervisor can use it in onSkipItem,
    // but wait, advance() is declared later... Let's declare a reference.
    // Actually, advance() is a suspend function, we can't easily reference it before it's declared if it's a local function.
    // Let's declare it as a var or just move the supervisor down?
    // Wait, let's just insert supervisor AFTER advance() is declared.

    DisposableEffect(players) {
        val listeners = players.associateWith { player ->
            object : Player.Listener {
                override fun onRenderedFirstFrame() {
                    player.videoDecoderCounters?.let { counters ->
                        counters.ensureUpdated()
                        visiblePlaybackBaselines[player] = DecoderSnapshot(
                            rendered = counters.renderedOutputBufferCount,
                            dropped = counters.droppedBufferCount
                        )
                    }
                }
            }.also(player::addListener)
        }
        onDispose {
            listeners.forEach { (player, listener) -> player.removeListener(listener) }
            players.forEach(ExoPlayer::release)
        }
    }

    // Anything left mid-play by a power cut or a process death is written down before the
    // first new item starts, using the last progress the checkpoint recorded.
    LaunchedEffect(Unit) { recoverInterruptedPlay(context) }

    val itemIds = playlist.joinToString(",") { it.id.toString() }
    LaunchedEffect(itemIds) {
        val preservedIndex = currentItemId?.let { id -> playlist.indexOfFirst { it.id == id } } ?: -1
        // A re-sync that drops the playing item abandons it mid-play. Clear the clock, because
        // the start-time guards below only stamp it when null: left set, the next advance()
        // would record the *incoming* item using the *outgoing* item's start time, attributing
        // the play to the wrong media and inflating its duration (so completed/partial too).
        if (preservedIndex < 0) {
            // It was on screen and the audience saw part of it, so it is a partial play
            // rather than nothing. Dropping it here quietly undercounted every screen whose
            // playlist was edited during the day. The checkpoint already holds everything
            // needed to write it, and is the same path a power cut recovers through.
            currentItemStartedAtMs = null
            recoverInterruptedPlay(context)
            // Immediately stop both players and reset transition progress to abort removed media in <100ms
            players[0].stop()
            players[0].playWhenReady = false
            players[1].stop()
            players[1].playWhenReady = false
            transitionProgress.snapTo(0f)
            currentIndex = 0
            currentItemId = playlist.firstOrNull()?.id
            playbackEpoch++
        } else {
            currentIndex = preservedIndex
            currentItemId = playlist[currentIndex].id
        }
    }

    val safeIndex = currentIndex.coerceIn(playlist.indices)
    val nextIndex = (safeIndex + 1) % playlist.size
    val currentItem = playlist[safeIndex]
    val nextItem = playlist[nextIndex]
    val incomingSlot = 1 - currentSlot
    val transitionSpec = TransitionSpecResolver.resolve(currentItem)

    LaunchedEffect(currentItem.id, nextItem.id, currentSlot, playbackEpoch) {
        preparePlayer(players[currentSlot], currentItem, autoPlay = currentItem.isVideo)
        if (playlist.size > 1) {
            preparePlayer(players[incomingSlot], nextItem, autoPlay = false)
        }
    }

    suspend fun recordPlayEvent(
        item: PlaylistItemEntity,
        startedAtMs: Long,
        reason: PlayEndReason,
        error: String? = null,
        finishedAtMs: Long = System.currentTimeMillis()
    ) {
        val durationMs = PlayCompletion.durationMs(startedAtMs, finishedAtMs)
        val prefs = context.getSharedPreferences("signage_prefs", android.content.Context.MODE_PRIVATE)
        val clock = com.olrac.signage.data.SignageClock.getInstance(context)
        val isTimeValid = clock.isClockVerified()
        val calculatedOffset = clock.currentEstimatedUtcMillis() - System.currentTimeMillis()
        val effectiveOffset = if (isTimeValid) calculatedOffset else null

        val formatter = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US).apply {
            timeZone = java.util.TimeZone.getTimeZone("UTC")
        }

        val effectiveReason = if (
            reason == PlayEndReason.PLAYED_TO_END &&
            !PlayCompletion.isPlausible(startedAtMs, finishedAtMs)
        ) PlayEndReason.INTERRUPTED else reason

        val recorded = PlayCompletion.describe(effectiveReason, error, isTimeValid)

        val eventId = prefs.getString(CHECKPOINT_EVENT_ID, null)
            ?.takeIf { prefs.getInt(CHECKPOINT_ITEM_ID, -1) == item.id }
            ?: java.util.UUID.randomUUID().toString()

        val event = com.olrac.signage.data.PlayEventEntity(
            eventId = eventId,
            mediaId = item.contentId.takeIf { it > 0 },
            playlistId = item.playlistId,
            campaignId = null,
            deviceStartedAt = formatter.format(java.util.Date(startedAtMs)),
            deviceFinishedAt = formatter.format(java.util.Date(finishedAtMs)),
            correctedStartedAt = formatter.format(java.util.Date(startedAtMs + (effectiveOffset ?: 0L))),
            correctedFinishedAt = formatter.format(java.util.Date(finishedAtMs + (effectiveOffset ?: 0L))),
            durationMs = durationMs,
            status = recorded.status,
            errorMessage = recorded.errorMessage,
            clockOffsetMs = effectiveOffset,
            playedOffline = !com.olrac.signage.network.ConnectivityWatcher.isOnline(context)
        )

        val dao = com.olrac.signage.data.AppDatabase.getDatabase(context).playEventDao()
        dao.insert(event)
        clearPlayCheckpoint(context)

        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            try {
                com.olrac.signage.service.ProofOfPlayReporter.flush(context)
            } catch (_: Exception) {}
        }
        com.olrac.signage.service.ProofOfPlayWorker.enqueueNow(context)

        if (playsSinceQueueCheck.incrementAndGet() >= QUEUE_CHECK_EVERY) {
            playsSinceQueueCheck.set(0)
            trimQueueIfOversized(dao)
        }
    }

    suspend fun advance(reason: PlayEndReason = PlayEndReason.PLAYED_TO_END) {
        transitionMutex.withLock {
            val activeIndex = currentIndex.coerceIn(playlist.indices)
            val outgoingItem = playlist[activeIndex]
            val outgoingSlot = currentSlot
            val outgoingPlayer = players[outgoingSlot]

            if (outgoingItem.isVideo) {
                outgoingPlayer.videoDecoderCounters?.let { counters ->
                    counters.ensureUpdated()
                    val baseline = visiblePlaybackBaselines[outgoingPlayer] ?: DecoderSnapshot(0, 0)
                    Log.i(
                        PERFORMANCE_TAG,
                        "item=${outgoingItem.id} rendered=${counters.renderedOutputBufferCount} " +
                            "dropped=${counters.droppedBufferCount} " +
                            "visibleRendered=${counters.renderedOutputBufferCount - baseline.rendered} " +
                            "visibleDropped=${counters.droppedBufferCount - baseline.dropped} " +
                            "maxConsecutiveDropped=${counters.maxConsecutiveDroppedBufferCount}"
                    )
                }
            }
            
            currentItemStartedAtMs?.let { startedAt ->
                val error = outgoingPlayer.playerError?.message
                recordPlayEvent(outgoingItem, startedAt, reason, error)
            }
            currentItemStartedAtMs = null
            clearPlayCheckpoint(context)

            if (playlist.size == 1) {
                if (outgoingItem.isVideo) {
                    outgoingPlayer.seekTo(0)
                    outgoingPlayer.playWhenReady = true
                }
                playbackEpoch++
                return@withLock
            }

            val targetIndex = (activeIndex + 1) % playlist.size
            val targetItem = playlist[targetIndex]
            val targetSlot = 1 - outgoingSlot
            val targetPlayer = players[targetSlot]
            preparePlayer(targetPlayer, targetItem, autoPlay = false)

            val targetReady = when {
                targetItem.isVideo -> awaitPlayerReady(targetPlayer)
                targetItem.isImage -> isImageDecodable(targetItem.localPath)
                else -> false
            }
            if (!targetReady) {
                telemetry.reportError(
                    targetItem.id,
                    "Unable to prepare ${targetItem.type} item ${targetItem.id}; cached media is missing or corrupt"
                )
                val fallbackIndex = (targetIndex + 1) % playlist.size
                if (fallbackIndex == activeIndex) {
                    playbackEpoch++
                } else {
                    currentIndex = fallbackIndex
                    currentItemId = playlist[fallbackIndex].id
                    playbackEpoch++
                }
                return@withLock
            }

            if (targetItem.isVideo) {
                targetPlayer.volume = 0f
                targetPlayer.playWhenReady = true
            }

            val spec = TransitionSpecResolver.resolve(outgoingItem)
            transitionProgress.snapTo(0f)
            if (spec.type != TransitionType.NONE) {
                transitionProgress.animateTo(
                    targetValue = 1f,
                    animationSpec = tween(durationMillis = spec.durationMs)
                ) {
                    outgoingPlayer.volume = 1f - value
                    targetPlayer.volume = value
                }
            }

            outgoingPlayer.playWhenReady = false
            outgoingPlayer.volume = 0f
            targetPlayer.volume = 1f
            currentSlot = targetSlot
            currentIndex = targetIndex
            currentItemId = targetItem.id
            transitionProgress.snapTo(0f)
            playbackEpoch++
        }
    }

    val supervisor = remember(playlist, players, context) {
        com.olrac.signage.service.PlayerSupervisor(
            context = context,
            players = players,
            telemetry = telemetry,
            currentItemId = { currentItemId },
            onSkipItem = { scope.launch { advance(PlayEndReason.SKIPPED) } },
            onReloadPlaylist = onReloadPlaylist
        )
    }

    DisposableEffect(supervisor) {
        supervisor.start()
        onDispose { supervisor.stop() }
    }

    LaunchedEffect(currentItem.id, currentSlot, playbackEpoch, transitionSpec) {
        if (currentItem.isImage) {
            if (!isImageDecodable(currentItem.localPath)) {
                telemetry.reportError(
                    currentItem.id,
                    "Unable to decode image item ${currentItem.id}; cached media is missing or corrupt"
                )
                delay(1_000L)
                advance(PlayEndReason.FAILED)
                return@LaunchedEffect
            }
            telemetry.reportPlaying(currentItem.id)
            if (currentItemStartedAtMs == null) currentItemStartedAtMs = System.currentTimeMillis()
            val imageStartedAt = currentItemStartedAtMs ?: System.currentTimeMillis()
            writePlayCheckpoint(context, currentItem, imageStartedAt, imageStartedAt)
            val totalMs = currentItem.duration.coerceAtLeast(1) * 1_000L
            var elapsed = 0L
            while (elapsed < totalMs) {
                val slice = minOf(CHECKPOINT_INTERVAL_MS, totalMs - elapsed)
                delay(slice)
                elapsed += slice
                writePlayCheckpoint(context, currentItem, imageStartedAt, System.currentTimeMillis())
            }
            advance(PlayEndReason.PLAYED_TO_END)
        } else if (currentItem.isVideo) {
            val activePlayer = players[currentSlot]
            val transitionLeadMs = if (transitionSpec.type == TransitionType.NONE) {
                100L
            } else {
                transitionSpec.durationMs + 120L
            }
            var reportedPlaying = false
            var lastCheckpointMs = 0L
            while (true) {
                val nowMs = System.currentTimeMillis()
                val durationMs = activePlayer.duration
                if (!reportedPlaying && activePlayer.playbackState == Player.STATE_READY) {
                    telemetry.reportPlaying(currentItem.id)
                    reportedPlaying = true
                    if (currentItemStartedAtMs == null) currentItemStartedAtMs = System.currentTimeMillis()
                }
                activePlayer.playerError?.let { error ->
                    telemetry.reportError(currentItem.id, error.message ?: "Video playback failed")
                }
                val failed = activePlayer.playerError != null
                val reachedEnd = activePlayer.playbackState == Player.STATE_ENDED ||
                    (durationMs != C.TIME_UNSET &&
                        durationMs > 0 &&
                        activePlayer.currentPosition >= (durationMs - transitionLeadMs).coerceAtLeast(0L))
                if (failed || reachedEnd) {
                    // The player knows why it is moving on, so the record says so directly.
                    // Reaching the planned hand-over point *is* a complete play: the lead
                    // time exists so the transition can cover the last frames, and the
                    // previous arithmetic check had no way to know that.
                    advance(if (failed) PlayEndReason.FAILED else PlayEndReason.PLAYED_TO_END)
                    break
                }
                currentItemStartedAtMs?.let { startedAt ->
                    if (nowMs - lastCheckpointMs >= CHECKPOINT_INTERVAL_MS) {
                        writePlayCheckpoint(context, currentItem, startedAt, nowMs)
                        lastCheckpointMs = nowMs
                    }
                }
                delay(16L)
            }
        }
    }

    BoxWithConstraints(modifier = Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
        val density = LocalDensity.current
        val screenW = maxWidth
        val screenH = maxHeight
        val widthPx = with(density) { screenW.toPx() }
        val heightPx = with(density) { screenH.toPx() }
        val progress = transitionProgress.value

        // Render by physical slot, not by current/next role. A preloaded video must
        // keep the same PlayerView/TextureView when it becomes current; moving it to
        // another composition position detaches the surface and creates a black flash.
        val slot0IsCurrent = currentSlot == 0
        PlaybackSurface(
            item = if (slot0IsCurrent) currentItem else nextItem,
            player = players[0],
            screenW = screenW,
            screenH = screenH,
            modifier = Modifier
                .transitionLayer(
                    transitionSpec,
                    progress,
                    incoming = !slot0IsCurrent,
                    widthPx,
                    heightPx
                )
        )
        if (playlist.size > 1) {
            PlaybackSurface(
                item = if (slot0IsCurrent) nextItem else currentItem,
                player = players[1],
                screenW = screenW,
                screenH = screenH,
                modifier = Modifier
                    .transitionLayer(
                        transitionSpec,
                        progress,
                        incoming = slot0IsCurrent,
                        widthPx,
                        heightPx
                    )
            )
        }
    }
}

private const val PERFORMANCE_TAG = "OlracPlaybackMetrics"
private const val TAG = "PlayerScreen"

/**
 * Ceiling on locally queued proof of play. Roughly a fortnight of continuous playback on a
 * six-second loop, so it is only ever reached by a screen that has been unable to reach the
 * server for a very long time.
 */
private const val MAX_QUEUED_PLAY_EVENTS = 200_000

/** How many plays between queue-size checks. */
private const val QUEUE_CHECK_EVERY = 200

private val playsSinceQueueCheck = java.util.concurrent.atomic.AtomicInteger(0)

/** How often the on-screen item's progress is written down. */
private const val CHECKPOINT_INTERVAL_MS = 5_000L

private const val CHECKPOINT_EVENT_ID = "current_play_event_id"
private const val CHECKPOINT_ITEM_ID = "current_play_item_id"
private const val CHECKPOINT_STARTED_AT = "current_play_started_at"
private const val CHECKPOINT_LAST_ALIVE = "current_play_last_alive"
private const val CHECKPOINT_CONTENT_ID = "current_play_content_id"
private const val CHECKPOINT_PLAYLIST_ID = "current_play_playlist_id"

/**
 * Drop the oldest queued plays if the backlog has grown past what the device should hold.
 *
 * Top level rather than a local function: Kotlin resolves local functions in declaration
 * order, so calling this from recordPlayEvent above required it to be declared first, and
 * it needs nothing from the composable's scope anyway.
 */
private suspend fun trimQueueIfOversized(dao: com.olrac.signage.data.PlayEventDao) {
    val queued = dao.countEvents()
    if (queued > MAX_QUEUED_PLAY_EVENTS) {
        val removed = dao.trimOldest(MAX_QUEUED_PLAY_EVENTS)
        Log.w(
            TAG,
            "Play event queue reached $queued; dropped $removed oldest events to protect the media cache"
        )
    }
}

/**
 * Note that an item is on screen, so a play interrupted by a power cut is not lost.
 *
 * A play was only ever written when the *next* one began, inside advance(). Pull the mains
 * lead on a signage screen -- which is how they are usually turned off -- and the advert
 * playing at that moment was never recorded. Always in the operator's favour and never the
 * advertiser's, which is the wrong way round for a number that gets invoiced.
 *
 * `lastAlive` is refreshed as the item plays, so the recovered record can say how far it
 * actually got rather than guessing.
 */
private suspend fun writePlayCheckpoint(
    context: android.content.Context,
    item: PlaylistItemEntity,
    startedAtMs: Long,
    lastAliveMs: Long
): String = withContext(Dispatchers.IO) {
    // Off the main thread. The durable commit below is a synchronous file write, and this
    // is called from the video loop that also drives frame timing -- a blocking write there
    // would show up as a stutter on exactly the cheap panels this app targets.
    val prefs = context.getSharedPreferences("signage_prefs", android.content.Context.MODE_PRIVATE)
    // One id per play, minted when the play starts and reused by whichever path ends up
    // writing the record.
    //
    // Without this the two paths could both fire: recordPlayEvent inserts the real event,
    // the mains is pulled before the checkpoint is cleared, and the next launch recovers
    // the same play as a second event with a fresh UUID -- one play counted twice, in the
    // direction that overstates delivery. Sharing the id makes the pair collapse: Room
    // REPLACEs on the primary key, and the server's ON CONFLICT DO NOTHING ignores the
    // loser. Whichever record lands first wins, and both describe the same play.
    val existing = prefs.getString(CHECKPOINT_EVENT_ID, null)
        ?.takeIf { prefs.getInt(CHECKPOINT_ITEM_ID, -1) == item.id && prefs.getLong(CHECKPOINT_STARTED_AT, 0L) == startedAtMs }
    val eventId = existing ?: java.util.UUID.randomUUID().toString()
    prefs.edit()
        .putString(CHECKPOINT_EVENT_ID, eventId)
        .putInt(CHECKPOINT_ITEM_ID, item.id)
        .putInt(CHECKPOINT_CONTENT_ID, item.contentId)
        .putInt(CHECKPOINT_PLAYLIST_ID, item.playlistId ?: -1)
        .putLong(CHECKPOINT_STARTED_AT, startedAtMs)
        .putLong(CHECKPOINT_LAST_ALIVE, lastAliveMs)
        // commit, not apply: a power cut kills the kernel, so an asynchronous write may
        // never reach disk -- and surviving exactly that is the whole point of this record.
        .commit()
    eventId
}

/**
 * Write down a play that was on screen when the app stopped, then clear the checkpoint.
 *
 * Called at launch (recovering a power cut or a process death) and when a re-sync drops the
 * playing item. Always recorded as a partial play: it demonstrably did not reach its end,
 * and `lastAlive` is the furthest point we can honestly claim it got to.
 *
 * Safe to call when there is nothing to recover, and safe to call twice -- the checkpoint
 * is removed as part of the same operation.
 */
private suspend fun recoverInterruptedPlay(context: android.content.Context) {
    val prefs = context.getSharedPreferences("signage_prefs", android.content.Context.MODE_PRIVATE)
    val itemId = prefs.getInt(CHECKPOINT_ITEM_ID, -1)
    val startedAt = prefs.getLong(CHECKPOINT_STARTED_AT, 0L)
    if (itemId < 0 || startedAt <= 0L) return

    val lastAlive = prefs.getLong(CHECKPOINT_LAST_ALIVE, startedAt).coerceAtLeast(startedAt)
    val contentId = prefs.getInt(CHECKPOINT_CONTENT_ID, -1)
    val playlistId = prefs.getInt(CHECKPOINT_PLAYLIST_ID, -1)
    // The id this play was given when it started. If recordPlayEvent already wrote the
    // real record before the process died, this insert collapses onto it rather than
    // counting the play a second time.
    val eventId = prefs.getString(CHECKPOINT_EVENT_ID, null) ?: java.util.UUID.randomUUID().toString()
    clearPlayCheckpoint(context)

    if (!PlayCompletion.isPlausible(startedAt, lastAlive)) {
        Log.w(TAG, "Discarding interrupted play for item $itemId: implausible timestamps")
        return
    }

    val offset = if (prefs.contains("server_time_offset_ms")) {
        prefs.getLong("server_time_offset_ms", 0L)
    } else {
        null
    }
    val formatter = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US).apply {
        timeZone = java.util.TimeZone.getTimeZone("UTC")
    }
    
    val isTimeValid = startedAt > 1704067200000L // Jan 1, 2024
    val finalError = if (!isTimeValid) "time_invalid_rtc_reset" else null
    val effectiveOffset = if (isTimeValid) offset else null

    val event = com.olrac.signage.data.PlayEventEntity(
        eventId = eventId,
        mediaId = contentId.takeIf { it > 0 },
        playlistId = playlistId.takeIf { it > 0 },
        campaignId = null,
        deviceStartedAt = formatter.format(java.util.Date(startedAt)),
        deviceFinishedAt = formatter.format(java.util.Date(lastAlive)),
        correctedStartedAt = formatter.format(java.util.Date(startedAt + (effectiveOffset ?: 0L))),
        correctedFinishedAt = formatter.format(java.util.Date(lastAlive + (effectiveOffset ?: 0L))),
        durationMs = PlayCompletion.durationMs(startedAt, lastAlive),
        status = PlayCompletion.status(PlayEndReason.INTERRUPTED, finalError),
        errorMessage = finalError,
        clockOffsetMs = effectiveOffset
        // playedOffline left null (the default): this is a play recovered after the app
        // restarted, not one observed finishing -- connectivity right now says nothing
        // about whether the network was up at the moment it actually ended.
    )
    com.olrac.signage.data.AppDatabase.getDatabase(context).playEventDao().insert(event)
    Log.i(TAG, "Recovered interrupted play for item $itemId (${event.durationMs}ms)")
}

private suspend fun clearPlayCheckpoint(context: android.content.Context) = withContext(Dispatchers.IO) {
    context.getSharedPreferences("signage_prefs", android.content.Context.MODE_PRIVATE)
        .edit()
        .remove(CHECKPOINT_EVENT_ID)
        .remove(CHECKPOINT_ITEM_ID)
        .remove(CHECKPOINT_CONTENT_ID)
        .remove(CHECKPOINT_PLAYLIST_ID)
        .remove(CHECKPOINT_STARTED_AT)
        .remove(CHECKPOINT_LAST_ALIVE)
        .commit()
    Unit
}

private data class DecoderSnapshot(val rendered: Int, val dropped: Int)

private suspend fun isImageDecodable(localPath: String?): Boolean = withContext(Dispatchers.IO) {
    if (localPath.isNullOrBlank()) return@withContext false
    val file = File(localPath)
    if (!file.isFile || file.length() == 0L) return@withContext false
    val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.absolutePath, options)
    if (options.outWidth > 0 && options.outHeight > 0) return@withContext true
    // Fallback: If BitmapFactory had difficulty (e.g. animated/special formats), allow non-empty file
    file.length() > 32L
}

@Composable
private fun PlaybackSurface(
    item: PlaylistItemEntity,
    player: ExoPlayer,
    screenW: Dp,
    screenH: Dp,
    modifier: Modifier = Modifier
) {
    val rotation = (item.rotation % 360 + 360) % 360
    val isSwapped = rotation == 90 || rotation == 270

    // When rotated 90/270 degrees, swap the canvas dimensions so that after rotation
    // it fills the physical display completely without letterboxing or squishing.
    val contentW = if (isSwapped) screenH else screenW
    val contentH = if (isSwapped) screenW else screenH

    // "cover" fills the panel and crops; "contain" shows the whole frame letterboxed.
    val imageScale = if (item.fitMode == "cover") ContentScale.Crop else ContentScale.Fit

    Box(
        modifier = modifier
            .size(contentW, contentH)
            .graphicsLayer {
                if (rotation != 0) {
                    rotationZ = rotation.toFloat()
                }
            },
        contentAlignment = Alignment.Center
    ) {
        when {
            item.isVideo -> AndroidView(
                factory = { context ->
                    (LayoutInflater.from(context).inflate(R.layout.player_surface, null, false) as PlayerView).apply {
                        setShutterBackgroundColor(AndroidColor.TRANSPARENT)
                        this.player = player
                        this.resizeMode = if (item.fitMode == "cover") {
                            androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_ZOOM
                        } else {
                            androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_FIT
                        }
                    }
                },
                update = {
                    it.player = player
                    it.resizeMode = if (item.fitMode == "cover") {
                        androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_ZOOM
                    } else {
                        androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_FIT
                    }
                },
                modifier = Modifier.fillMaxSize()
            )

            item.isImage -> AsyncImage(
                model = item.localPath?.let(::File),
                contentDescription = "Signage image",
                contentScale = imageScale,
                modifier = Modifier.fillMaxSize()
            )

            else -> Box(modifier = Modifier.fillMaxSize().background(Color.Black))
        }
    }
}

private fun Modifier.transitionLayer(
    spec: TransitionSpec,
    progress: Float,
    incoming: Boolean,
    widthPx: Float,
    heightPx: Float
): Modifier = graphicsLayer {
    when (spec.type) {
        TransitionType.NONE -> alpha = if (incoming) 0f else 1f
        TransitionType.FADE -> alpha = if (incoming) progress else 1f - progress
        TransitionType.ZOOM -> {
            alpha = if (incoming) progress else 1f - progress
            val scale = if (incoming) 0.9f + (0.1f * progress) else 1f + (0.1f * progress)
            scaleX = scale
            scaleY = scale
        }

        TransitionType.SLIDE_LEFT -> translationX = if (incoming) {
            widthPx * (1f - progress)
        } else {
            -widthPx * progress
        }

        TransitionType.SLIDE_RIGHT -> translationX = if (incoming) {
            -widthPx * (1f - progress)
        } else {
            widthPx * progress
        }

        TransitionType.SLIDE_UP -> translationY = if (incoming) {
            heightPx * (1f - progress)
        } else {
            -heightPx * progress
        }

        TransitionType.SLIDE_DOWN -> translationY = if (incoming) {
            -heightPx * (1f - progress)
        } else {
            heightPx * progress
        }
    }
}

private fun preparePlayer(player: ExoPlayer, item: PlaylistItemEntity, autoPlay: Boolean) {
    if (!item.isVideo || item.localPath.isNullOrBlank()) {
        player.pause()
        player.clearMediaItems()
        return
    }

    val file = File(item.localPath)
    if (!file.isFile || file.length() == 0L) {
        player.pause()
        player.clearMediaItems()
        return
    }

    val mediaId = item.id.toString()
    if (player.currentMediaItem?.mediaId != mediaId) {
        player.setMediaItem(
            MediaItem.Builder()
                .setMediaId(mediaId)
                .setUri(Uri.fromFile(file))
                .build()
        )
        player.prepare()
    } else if (player.playbackState == Player.STATE_ENDED) {
        player.seekTo(0)
        player.prepare()
    }
    player.volume = if (autoPlay) 1f else 0f
    player.playWhenReady = autoPlay
}

private suspend fun awaitPlayerReady(player: ExoPlayer): Boolean {
    if (player.playbackState == Player.STATE_READY) return true
    return withTimeoutOrNull(10_000L) {
        suspendCancellableCoroutine { continuation ->
            val listener = object : Player.Listener {
                override fun onPlaybackStateChanged(playbackState: Int) {
                    if (playbackState == Player.STATE_READY && continuation.isActive) {
                        player.removeListener(this)
                        continuation.resume(true)
                    }
                }

                override fun onPlayerError(error: PlaybackException) {
                    if (continuation.isActive) {
                        player.removeListener(this)
                        continuation.resume(false)
                    }
                }
            }
            player.addListener(listener)
            continuation.invokeOnCancellation { player.removeListener(listener) }
        }
    } ?: false
}
