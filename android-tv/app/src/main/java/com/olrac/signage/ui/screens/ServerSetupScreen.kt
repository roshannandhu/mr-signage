package com.olrac.signage.ui.screens

import android.content.Context
import android.content.Intent
import android.os.Environment
import android.os.StatFs
import android.provider.Settings
import android.view.KeyEvent
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Home
import androidx.compose.material.icons.rounded.Info
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.PowerSettingsNew
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.icons.rounded.Timer
import androidx.compose.material.icons.rounded.Tv
import androidx.compose.material.icons.rounded.Warning
import androidx.compose.material.ripple.rememberRipple
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.olrac.signage.data.DeviceState
import kotlinx.coroutines.delay

/** Delay to discard gestures still in flight when the setup screen opens. */
private const val INPUT_ARM_DELAY_MS = 350L

/**
 * Ultra-Modern OLED Maintenance Hub.
 *
 * Professional fleet maintenance screen:
 * - OLED Deep Black background (#07090E) with sleek glass cards
 * - Fully scrollable: never clips or cuts off content on any screen aspect ratio
 * - Auto-Relock Countdown Pill with pulsing amber warning at 30s
 * - Prominent "Return to Playback & Lock" hero card
 * - Clean system launchers: Android System Settings, TV Home exit
 * - Live device telemetry with full non-truncated specs
 * - Quarantined Danger Zone with modal confirmation
 */
@Composable
fun ServerSetupScreen(
    defaultHome: Boolean = false,
    onChooseHome: () -> Unit = {},
    onExit: () -> Unit = {},
    onUnlink: () -> Unit = {},
    onClose: () -> Unit = {}
) {
    BackHandler(onBack = onClose)
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE) }
    val screenName = prefs.getString("screen_name", "Screen") ?: "Screen"
    val brandName = prefs.getString("brand_name", "MR Signage") ?: "MR Signage"
    val screenId = prefs.getInt("screen_id", -1)
    val credentialsVersion = prefs.getInt("credentials_version", 1)

    var armed by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        delay(INPUT_ARM_DELAY_MS)
        armed = true
    }

    // Inactivity countdown: 5 minutes (300 seconds)
    var remainingSeconds by remember { mutableIntStateOf(300) }
    LaunchedEffect(Unit) {
        while (remainingSeconds > 0) {
            delay(1000L)
            remainingSeconds--
        }
        onClose()
    }

    var showUnlinkDialog by remember { mutableStateOf(false) }
    val freeStorageGb = remember {
        try {
            val stat = StatFs(Environment.getDataDirectory().path)
            val free = stat.availableBlocksLong * stat.blockSizeLong
            val total = stat.blockCountLong * stat.blockSizeLong
            String.format("%.1f GB free / %.1f GB total", free / (1024.0 * 1024.0 * 1024.0), total / (1024.0 * 1024.0 * 1024.0))
        } catch (_: Exception) {
            "Available"
        }
    }
    val ipAddress = remember {
        try {
            var ip = "Connected"
            val interfaces = java.net.NetworkInterface.getNetworkInterfaces()
            while (interfaces.hasMoreElements()) {
                val intf = interfaces.nextElement()
                val addrs = intf.inetAddresses
                while (addrs.hasMoreElements()) {
                    val addr = addrs.nextElement()
                    if (!addr.isLoopbackAddress && addr is java.net.Inet4Address) {
                        ip = addr.hostAddress ?: "Connected"
                        break
                    }
                }
            }
            ip
        } catch (_: Exception) {
            "Connected"
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF07090E))
            .pointerInput(armed) {
                if (!armed) awaitPointerEventScope {
                    while (true) awaitPointerEvent(PointerEventPass.Initial).changes.forEach { it.consume() }
                }
            }
            .onPreviewKeyEvent { !armed }
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // ==========================================
            // TOP HEADER BAR: Identity, Status, Countdown, Close
            // ==========================================
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    // Identity Brand Block
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.weight(1f, fill = false)
                    ) {
                        Box(
                            contentAlignment = Alignment.Center,
                            modifier = Modifier
                                .size(38.dp)
                                .background(
                                    Brush.linearGradient(listOf(Color(0xFF10B981), Color(0xFF06B6D4))),
                                    CircleShape
                                )
                        ) {
                            Icon(
                                imageVector = Icons.Rounded.Tv,
                                contentDescription = null,
                                tint = Color(0xFF061A14),
                                modifier = Modifier.size(22.dp)
                            )
                        }

                        Spacer(modifier = Modifier.width(12.dp))

                        Column {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    text = brandName.uppercase(),
                                    color = Color(0xFF10B981),
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.SansSerif,
                                    letterSpacing = 1.2.sp
                                )
                                Text(
                                    text = " • MAINTENANCE HUB",
                                    color = Color(0xFF64748B),
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    fontFamily = FontFamily.SansSerif
                                )
                            }
                            Text(
                                text = "$screenName (#$screenId)",
                                color = Color.White,
                                fontSize = 16.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.SansSerif
                            )
                        }
                    }

                    // Top Action Controls
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        // Synced Pill
                        Surface(
                            shape = RoundedCornerShape(16.dp),
                            color = Color(0x1A10B981),
                            border = BorderStroke(1.dp, Color(0xFF10B981).copy(alpha = 0.4f))
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp)
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(6.dp)
                                        .background(Color(0xFF10B981), CircleShape)
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = "v$credentialsVersion Synced",
                                    color = Color(0xFF6EE7B7),
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    fontFamily = FontFamily.SansSerif
                                )
                            }
                        }

                        // Countdown Timer Pill
                        AutoRelockTimerPill(
                            seconds = remainingSeconds,
                            onClick = { remainingSeconds = 300 }
                        )

                        // Close Button
                        Surface(
                            shape = RoundedCornerShape(16.dp),
                            color = Color(0xFF1E293B),
                            border = BorderStroke(1.dp, Color(0xFF334155)),
                            modifier = Modifier
                                .clickable(
                                    interactionSource = remember { MutableInteractionSource() },
                                    indication = rememberRipple(bounded = true, color = Color.White),
                                    onClick = onClose
                                )
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp)
                            ) {
                                Icon(
                                    imageVector = Icons.Rounded.Close,
                                    contentDescription = "Close",
                                    tint = Color.White,
                                    modifier = Modifier.size(14.dp)
                                )
                                Spacer(modifier = Modifier.width(4.dp))
                                Text(
                                    text = "Close",
                                    color = Color.White,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    fontFamily = FontFamily.SansSerif
                                )
                            }
                        }
                    }
                }
            }

            // ==========================================
            // 1. HERO ACTION CARD: Return & Lock
            // ==========================================
            HeroLockAndResumeCard(onClick = onClose)

            // ==========================================
            // 2. SYSTEM LAUNCHERS & CONTROLS CARD
            // ==========================================
            SystemControlsCard(
                defaultHome = defaultHome,
                onChooseHome = onChooseHome,
                onExit = onExit
            )

            // ==========================================
            // 3. HARDWARE & NETWORK TELEMETRY CARD
            // ==========================================
            DeviceTelemetryCard(
                ipAddress = ipAddress,
                freeStorage = freeStorageGb,
                screenId = screenId
            )

            // ==========================================
            // 4. DANGER ZONE: Switch Account / Unlink
            // ==========================================
            DangerZoneCard(onUnlinkClick = { showUnlinkDialog = true })

            Spacer(modifier = Modifier.height(16.dp))
        }
    }

    if (showUnlinkDialog) {
        UnlinkConfirmationDialog(
            onConfirm = {
                showUnlinkDialog = false
                onUnlink()
            },
            onDismiss = { showUnlinkDialog = false }
        )
    }
}

/**
 * Top Auto-Relock Countdown Pill.
 */
@Composable
private fun AutoRelockTimerPill(
    seconds: Int,
    onClick: () -> Unit
) {
    val mins = seconds / 60
    val secs = seconds % 60
    val formatted = String.format("%d:%02d", mins, secs)
    val isUrgent = seconds in 1..30

    val infiniteTransition = rememberInfiniteTransition(label = "timerPulse")
    val alphaAnim by infiniteTransition.animateFloat(
        initialValue = 1f,
        targetValue = 0.35f,
        animationSpec = infiniteRepeatable(
            animation = tween(500, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "timerAlpha"
    )

    val pillColor = if (isUrgent) Color(0xFFF59E0B) else Color(0xFF94A3B8)
    val pillBg = if (isUrgent) Color(0x26F59E0B) else Color(0x1F334155)

    Surface(
        shape = RoundedCornerShape(16.dp),
        color = pillBg,
        border = BorderStroke(1.dp, pillColor.copy(alpha = if (isUrgent) alphaAnim else 0.4f)),
        modifier = Modifier.clickable(
            interactionSource = remember { MutableInteractionSource() },
            indication = rememberRipple(bounded = true, color = pillColor),
            onClick = onClick
        )
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp)
        ) {
            Icon(
                imageVector = Icons.Rounded.Timer,
                contentDescription = null,
                tint = pillColor,
                modifier = Modifier.size(13.dp)
            )
            Spacer(modifier = Modifier.width(5.dp))
            Text(
                text = formatted,
                color = pillColor,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.SansSerif
            )
        }
    }
}

/**
 * Hero Action Card: Tap to immediately re-arm kiosk lock and resume ad loop.
 */
@Composable
private fun HeroLockAndResumeCard(onClick: () -> Unit) {
    var isFocused by remember { mutableStateOf(false) }

    Surface(
        shape = RoundedCornerShape(18.dp),
        color = Color(0xFF062319),
        border = BorderStroke(1.2.dp, if (isFocused) Color(0xFF34D399) else Color(0xFF10B981).copy(alpha = 0.7f)),
        shadowElevation = if (isFocused) 10.dp else 4.dp,
        modifier = Modifier
            .fillMaxWidth()
            .scale(if (isFocused) 1.01f else 1f)
            .onFocusChanged { isFocused = it.isFocused }
            .focusable(true)
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = rememberRipple(bounded = true, color = Color(0xFF10B981)),
                onClick = onClick
            )
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(18.dp)
        ) {
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier
                    .size(48.dp)
                    .background(Color(0xFF10B981), CircleShape)
            ) {
                Icon(
                    imageVector = Icons.Rounded.PlayArrow,
                    contentDescription = null,
                    tint = Color(0xFF041812),
                    modifier = Modifier.size(30.dp)
                )
            }

            Spacer(modifier = Modifier.width(16.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Return to Playback & Lock Screen",
                    color = Color.White,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.SansSerif
                )
                Spacer(modifier = Modifier.height(3.dp))
                Text(
                    text = "Re-arms kiosk security and immediately resumes fullscreen advertisement loop.",
                    color = Color(0xFF86EFAC),
                    fontSize = 12.sp,
                    lineHeight = 16.sp,
                    fontFamily = FontFamily.SansSerif
                )
            }
        }
    }
}

/**
 * System Controls Card: TV launcher status, Android Settings, and Home Exit.
 */
@Composable
private fun SystemControlsCard(
    defaultHome: Boolean,
    onChooseHome: () -> Unit,
    onExit: () -> Unit
) {
    val context = LocalContext.current

    Surface(
        shape = RoundedCornerShape(16.dp),
        color = Color(0xFF0D121D),
        border = BorderStroke(1.dp, Color(0xFF1E283D)),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            // Card Title
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = Icons.Rounded.Settings,
                    contentDescription = null,
                    tint = Color(0xFF38BDF8),
                    modifier = Modifier.size(18.dp)
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "TV SYSTEM & REBOOT INTEGRATION",
                    color = Color(0xFF94A3B8),
                    fontSize = 11.5.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.SansSerif,
                    letterSpacing = 0.8.sp
                )
            }

            // Status Badges Row
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                // TV Launcher indicator
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = Color(0xFF131A29),
                    border = BorderStroke(1.dp, Color(0xFF1E293B)),
                    modifier = Modifier
                        .weight(1f)
                        .clickable(enabled = !defaultHome, onClick = onChooseHome)
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(7.dp)
                                .background(if (defaultHome) Color(0xFF10B981) else Color(0xFFF59E0B), CircleShape)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Column {
                            Text(
                                text = "Default TV Launcher",
                                color = Color.White,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold,
                                fontFamily = FontFamily.SansSerif
                            )
                            Text(
                                text = if (defaultHome) "Configured as default" else "Tap to set as default",
                                color = if (defaultHome) Color(0xFF6EE7B7) else Color(0xFFFBBF24),
                                fontSize = 10.5.sp,
                                fontFamily = FontFamily.SansSerif
                            )
                        }
                    }
                }

                // Reboot Auto-Start indicator
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = Color(0xFF131A29),
                    border = BorderStroke(1.dp, Color(0xFF1E293B)),
                    modifier = Modifier.weight(1f)
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(7.dp)
                                .background(Color(0xFF10B981), CircleShape)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Column {
                            Text(
                                text = "Reboot Auto-Start",
                                color = Color.White,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold,
                                fontFamily = FontFamily.SansSerif
                            )
                            Text(
                                text = "Display over apps allowed",
                                color = Color(0xFF6EE7B7),
                                fontSize = 10.5.sp,
                                fontFamily = FontFamily.SansSerif
                            )
                        }
                    }
                }
            }

            // Quick Action Launchers Row
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                // Button 1: Android System Settings
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = Color(0xFF182235),
                    border = BorderStroke(1.dp, Color(0xFF2E3E5B)),
                    modifier = Modifier
                        .weight(1f)
                        .clickable {
                            try {
                                context.startActivity(Intent(Settings.ACTION_SETTINGS).apply {
                                    flags = Intent.FLAG_ACTIVITY_NEW_TASK
                                })
                            } catch (_: Exception) {}
                        }
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 12.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Rounded.Settings,
                            contentDescription = null,
                            tint = Color(0xFF60A5FA),
                            modifier = Modifier.size(18.dp)
                        )
                        Spacer(modifier = Modifier.width(10.dp))
                        Column {
                            Text(
                                text = "Android Settings",
                                color = Color.White,
                                fontSize = 12.5.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.SansSerif
                            )
                            Text(
                                text = "Wi-Fi, Bluetooth, Display",
                                color = Color(0xFF94A3B8),
                                fontSize = 10.5.sp,
                                fontFamily = FontFamily.SansSerif
                            )
                        }
                    }
                }

                // Button 2: Exit to System Launcher
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = Color(0xFF182235),
                    border = BorderStroke(1.dp, Color(0xFF2E3E5B)),
                    modifier = Modifier
                        .weight(1f)
                        .clickable(onClick = onExit)
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 12.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Rounded.Home,
                            contentDescription = null,
                            tint = Color(0xFFA78BFA),
                            modifier = Modifier.size(18.dp)
                        )
                        Spacer(modifier = Modifier.width(10.dp))
                        Column {
                            Text(
                                text = "Home Launcher",
                                color = Color.White,
                                fontSize = 12.5.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.SansSerif
                            )
                            Text(
                                text = "Exit to Android Home",
                                color = Color(0xFF94A3B8),
                                fontSize = 10.5.sp,
                                fontFamily = FontFamily.SansSerif
                            )
                        }
                    }
                }
            }
        }
    }
}

/**
 * Telemetry Card: Displays full specs without truncation.
 */
@Composable
private fun DeviceTelemetryCard(
    ipAddress: String,
    freeStorage: String,
    screenId: Int
) {
    Surface(
        shape = RoundedCornerShape(16.dp),
        color = Color(0xFF0D121D),
        border = BorderStroke(1.dp, Color(0xFF1E283D)),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = Icons.Rounded.Info,
                    contentDescription = null,
                    tint = Color(0xFF38BDF8),
                    modifier = Modifier.size(18.dp)
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "DEVICE TELEMETRY & HARDWARE SPECS",
                    color = Color(0xFF94A3B8),
                    fontSize = 11.5.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.SansSerif,
                    letterSpacing = 0.8.sp
                )
            }

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color(0xFF131A29), RoundedCornerShape(12.dp))
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                TelemetrySpecRow("Device IP Address", ipAddress)
                TelemetrySpecRow("Storage", freeStorage)
                TelemetrySpecRow("Display", "1280 × 800 (60 Hz)")
                TelemetrySpecRow("Backend Cloud", "mr-signage.onrender.com")
                TelemetrySpecRow("Kiosk Security", "Temporarily Unlocked for Maintenance")
            }
        }
    }
}

@Composable
private fun TelemetrySpecRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = label,
            color = Color(0xFF64748B),
            fontSize = 12.sp,
            fontFamily = FontFamily.SansSerif
        )
        Text(
            text = value,
            color = Color.White,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
            fontFamily = FontFamily.SansSerif
        )
    }
}

/**
 * Danger Zone Card: Quarantined at the bottom for safety.
 */
@Composable
private fun DangerZoneCard(onUnlinkClick: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(16.dp),
        color = Color(0xFF140D10),
        border = BorderStroke(1.dp, Color(0x38EF4444)),
        modifier = Modifier.fillMaxWidth()
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(18.dp)
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        imageVector = Icons.Rounded.Warning,
                        contentDescription = null,
                        tint = Color(0xFFEF4444),
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        text = "DANGER ZONE",
                        color = Color(0xFFF87171),
                        fontSize = 11.5.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.SansSerif,
                        letterSpacing = 0.8.sp
                    )
                }
                Spacer(modifier = Modifier.height(3.dp))
                Text(
                    text = "Disconnects this screen from your workspace and clears local credentials.",
                    color = Color(0xFF94A3B8),
                    fontSize = 11.5.sp,
                    lineHeight = 15.sp,
                    fontFamily = FontFamily.SansSerif
                )
            }

            Spacer(modifier = Modifier.width(14.dp))

            Surface(
                shape = RoundedCornerShape(10.dp),
                color = Color(0x33EF4444),
                border = BorderStroke(1.dp, Color(0xFFEF4444)),
                modifier = Modifier.clickable(onClick = onUnlinkClick)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)
                ) {
                    Icon(
                        imageVector = Icons.Rounded.PowerSettingsNew,
                        contentDescription = null,
                        tint = Color(0xFFFCA5A5),
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        text = "Unlink Screen",
                        color = Color.White,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.SansSerif
                    )
                }
            }
        }
    }
}

/**
 * Confirmation dialog for unlinking the screen.
 */
@Composable
private fun UnlinkConfirmationDialog(
    onConfirm: () -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = "Unlink Screen?",
                color = Color.White,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.SansSerif
            )
        },
        text = {
            Text(
                text = "This screen will be disconnected from Legit Forge. All cached media and workspace pairing credentials will be wiped, returning the screen to provisioning.",
                color = Color(0xFF94A3B8),
                fontSize = 13.sp,
                fontFamily = FontFamily.SansSerif
            )
        },
        confirmButton = {
            Button(
                onClick = onConfirm,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFEF4444))
            ) {
                Text(
                    text = "Confirm Unlink",
                    color = Color.White,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.SansSerif
                )
            }
        },
        dismissButton = {
            OutlinedButton(onClick = onDismiss) {
                Text(
                    text = "Cancel",
                    color = Color.White,
                    fontFamily = FontFamily.SansSerif
                )
            }
        },
        containerColor = Color(0xFF0F141F)
    )
}

/**
 * Preserved for GoogleSignInScreen compatibility.
 */
@Composable
fun ServerControls(
    serverUrl: String,
    onSave: (String) -> Unit,
    modifier: Modifier = Modifier,
    serverError: String? = null,
    defaultHome: Boolean = false,
    onChooseHome: () -> Unit = {}
) {
    Surface(
        color = Color(0xFF0F141F),
        modifier = modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = "Server Configuration",
                color = Color.White,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.SansSerif
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = serverUrl,
                color = Color(0xFF94A3B8),
                fontFamily = FontFamily.SansSerif
            )
        }
    }
}
