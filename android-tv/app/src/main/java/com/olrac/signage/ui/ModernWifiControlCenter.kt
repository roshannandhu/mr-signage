package com.olrac.signage.ui

import android.content.res.Configuration
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.LockOpen
import androidx.compose.material.icons.rounded.NetworkWifi
import androidx.compose.material.icons.rounded.NetworkWifi1Bar
import androidx.compose.material.icons.rounded.NetworkWifi2Bar
import androidx.compose.material.icons.rounded.NetworkWifi3Bar
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.SignalWifi0Bar
import androidx.compose.material.icons.rounded.WifiOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.olrac.signage.network.WifiManagerHelper
import com.olrac.signage.network.WifiNetworkItem
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

// ─────────────────────────────────────────────────────────────────────────────
// Theme colours
// ─────────────────────────────────────────────────────────────────────────────

private val PureBlack       = Color(0xFF000000)
private val DarkCard        = Color(0xFF0D0D0D)
private val DarkCardFocused = Color(0xFF1A1A1A)
private val DarkBorder      = Color(0xFF2A2A2A)
private val ElectricGreen   = Color(0xFF22C55E)
private val AmberWarn       = Color(0xFFF59E0B)
private val OrangeWeak      = Color(0xFFFB923C)
private val CrimsonRed      = Color(0xFFEF4444)
private val TextMuted       = Color(0xFF888888)
private val TextDim         = Color(0xFF555555)

// ─────────────────────────────────────────────────────────────────────────────
// Wi-Fi Icon — Material Design Rounded, signal-aware
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns the correct Material Rounded Wi-Fi icon and tint for the signal level.
 *
 * | bars | connected | icon                            | colour        |
 * |------|-----------|---------------------------------|---------------|
 * | 4    | yes       | SignalWifi4Bar                  | ElectricGreen |
 * | 3    | yes       | SignalWifiStatusbar3Bar          | ElectricGreen |
 * | 2    | yes       | SignalWifiStatusbar2Bar          | Amber         |
 * | 1    | yes       | SignalWifiStatusbar1Bar          | Orange        |
 * | 0    | yes       | SignalWifi0Bar                  | CrimsonRed    |
 * | any  | no        | WifiOff / NoInternet4           | CrimsonRed    |
 */
@Composable
fun WifiSignalIcon(
    isConnected: Boolean,
    signalBars: Int,
    modifier: Modifier = Modifier,
    size: Dp = 24.dp,
) {
    val (icon: ImageVector, tint: Color) = when {
        !isConnected -> Icons.Rounded.WifiOff to CrimsonRed
        signalBars >= 4 -> Icons.Rounded.NetworkWifi to ElectricGreen
        signalBars == 3 -> Icons.Rounded.NetworkWifi3Bar to ElectricGreen
        signalBars == 2 -> Icons.Rounded.NetworkWifi2Bar to AmberWarn
        signalBars == 1 -> Icons.Rounded.NetworkWifi1Bar to OrangeWeak
        else -> Icons.Rounded.SignalWifi0Bar to CrimsonRed
    }

    Icon(
        imageVector = icon,
        contentDescription = if (isConnected) "Wi-Fi signal $signalBars bars" else "Wi-Fi disconnected",
        tint = tint,
        modifier = modifier.size(size),
    )
}

// ─────────────────────────────────────────────────────────────────────────────
// Floating HUD — corner overlay shown on touch/keypress, auto-hides
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Minimal floating Wi-Fi indicator.
 * Appears when [visible] is true, auto-disappears after 12 seconds of inactivity.
 * Shows a pulsing ring while offline to draw attention.
 */
@Composable
fun FloatingWifiSymbolHud(
    visible: Boolean,
    isConnected: Boolean,
    signalBars: Int,
    onTap: () -> Unit,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = visible,
        enter = scaleIn(spring(Spring.DampingRatioMediumBouncy)) + fadeIn(tween(180)),
        exit  = scaleOut(tween(160))                            + fadeOut(tween(160)),
        modifier = modifier,
    ) {
        // Pulsing glow ring when disconnected
        val pulse = rememberInfiniteTransition(label = "hudPulse")
        val pulseAlpha by pulse.animateFloat(
            initialValue = 0.25f, targetValue = 0.75f,
            animationSpec = infiniteRepeatable(tween(900, easing = FastOutSlowInEasing), RepeatMode.Reverse),
            label = "pulseAlpha",
        )

        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier
                .size(48.dp)
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null,
                    onClick = onTap,
                ),
        ) {
            if (!isConnected) {
                // Pulsing red background ring
                Surface(
                    shape = RoundedCornerShape(50),
                    color = CrimsonRed.copy(alpha = pulseAlpha * 0.3f),
                    modifier = Modifier.size(44.dp),
                ) {}
            }
            WifiSignalIcon(isConnected = isConnected, signalBars = signalBars, size = 28.dp)
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Wi-Fi Dialog
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun ModernWifiControlCenterDialog(
    wifiHelper: WifiManagerHelper,
    onDismiss: () -> Unit,
) {
    val configuration = LocalConfiguration.current
    val isPortrait = configuration.orientation == Configuration.ORIENTATION_PORTRAIT
    val scope = rememberCoroutineScope()

    var isWifiOn          by remember { mutableStateOf(wifiHelper.isWifiEnabled) }
    var networks          by remember { mutableStateOf<List<WifiNetworkItem>>(emptyList()) }
    var selectedNetwork   by remember { mutableStateOf<WifiNetworkItem?>(null) }
    var isPasswordDialogOpen by remember { mutableStateOf(false) }
    var connectingToSsid  by remember { mutableStateOf<String?>(null) }
    var statusMessage     by remember { mutableStateOf<String?>(null) }
    var isScanning        by remember { mutableStateOf(false) }
    var scanCooldownSec   by remember { mutableStateOf(0) }

    // Register receivers and kick off initial scan.
    LaunchedEffect(Unit) {
        wifiHelper.registerReceivers()
        wifiHelper.onWifiStateChanged = { enabled ->
            isWifiOn = enabled
        }
        wifiHelper.onNetworksUpdated = { updated ->
            networks  = updated
            isScanning = false
        }
        wifiHelper.onScanThrottled = {
            // Show remaining cooldown in the button label.
            scope.launch {
                val rem = wifiHelper.scanCooldownRemainingMs()
                scanCooldownSec = ((rem + 999) / 1000).toInt()
                while (scanCooldownSec > 0) {
                    delay(1_000)
                    scanCooldownSec--
                }
            }
            isScanning = false
        }
        isScanning = true
        wifiHelper.startScan()
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(
            usePlatformDefaultWidth  = false,
            dismissOnBackPress       = true,
            dismissOnClickOutside    = true,
        ),
    ) {
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 0.88f))
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null,
                    onClick = onDismiss,
                ),
        ) {
            BoxWithConstraints(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(if (isPortrait) 16.dp else 24.dp),
                contentAlignment = Alignment.Center,
            ) {
                val isLandscapeWide = !isPortrait && maxWidth > 650.dp
                val dialogWidth  = if (isPortrait) maxWidth else maxWidth.coerceAtMost(880.dp)
                val dialogHeight = if (isPortrait) maxHeight * 0.90f else maxHeight.coerceAtMost(560.dp)

                // Slide-up spring entrance
                AnimatedVisibility(
                    visible = true,
                    enter   = slideInVertically(spring(dampingRatio = 0.72f, stiffness = 420f)) { it / 3 } + fadeIn(tween(220)),
                    exit    = slideOutVertically(tween(180)) { it / 4 } + fadeOut(tween(160)),
                ) {
                    Surface(
                        shape         = RoundedCornerShape(22.dp),
                        color         = PureBlack,
                        border        = BorderStroke(1.dp, DarkBorder),
                        shadowElevation = 32.dp,
                        modifier      = Modifier
                            .width(dialogWidth)
                            .height(dialogHeight)
                            .clickable(
                                interactionSource = remember { MutableInteractionSource() },
                                indication        = null,
                                onClick           = {},
                            ),
                    ) {
                        Column(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(20.dp),
                        ) {
                            // ── Header ───────────────────────────────────────
                            WifiHeaderBar(
                                isWifiOn        = isWifiOn,
                                isScanning      = isScanning,
                                scanCooldownSec = scanCooldownSec,
                                isConnected     = wifiHelper.isWifiConnected,
                                signalBars      = wifiHelper.currentSignalBars,
                                onToggleWifi    = { enable ->
                                    wifiHelper.setWifiEnabled(enable)
                                    isWifiOn = enable
                                    if (enable) { isScanning = true; wifiHelper.startScan() }
                                },
                                onRefresh       = {
                                    isScanning = true
                                    val started = wifiHelper.startScan()
                                    if (!started) isScanning = false
                                },
                                onClose         = onDismiss,
                            )

                            Spacer(Modifier.height(14.dp))

                            // ── Status banner ────────────────────────────────
                            AnimatedVisibility(
                                visible = statusMessage != null,
                                enter   = slideInVertically { -it / 2 } + fadeIn(tween(200)),
                                exit    = fadeOut(tween(180)),
                            ) {
                                statusMessage?.let { msg ->
                                    val isSuccess = msg.contains("Connected", ignoreCase = true)
                                    Surface(
                                        shape  = RoundedCornerShape(10.dp),
                                        color  = if (isSuccess) ElectricGreen.copy(0.12f) else CrimsonRed.copy(0.14f),
                                        border = BorderStroke(1.dp, if (isSuccess) ElectricGreen else CrimsonRed),
                                        modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
                                    ) {
                                        Text(
                                            text       = msg,
                                            color      = if (isSuccess) ElectricGreen else Color.White,
                                            fontSize   = 13.sp,
                                            fontWeight = FontWeight.Medium,
                                            modifier   = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
                                        )
                                    }
                                }
                            }

                            // ── Body ─────────────────────────────────────────
                            if (!isWifiOn) {
                                WifiDisabledEmptyState(onEnableWifi = {
                                    wifiHelper.setWifiEnabled(true)
                                    isWifiOn   = true
                                    isScanning = true
                                    wifiHelper.startScan()
                                })
                            } else if (isLandscapeWide) {
                                Row(
                                    modifier              = Modifier.fillMaxWidth().weight(1f),
                                    horizontalArrangement = Arrangement.spacedBy(16.dp),
                                ) {
                                    CurrentConnectionStatusCard(
                                        connectedSsid = wifiHelper.currentConnectedSsid,
                                        signalBars    = wifiHelper.currentSignalBars,
                                        modifier      = Modifier.weight(0.38f).fillMaxHeight(),
                                    )
                                    AvailableNetworksList(
                                        networks         = networks,
                                        isScanning       = isScanning,
                                        connectingToSsid = connectingToSsid,
                                        onSelectNetwork  = { network ->
                                            selectedNetwork      = network
                                            isPasswordDialogOpen = true
                                        },
                                        modifier = Modifier.weight(0.62f).fillMaxHeight(),
                                    )
                                }
                            } else {
                                Column(modifier = Modifier.fillMaxWidth().weight(1f)) {
                                    CurrentConnectionStatusPill(
                                        connectedSsid = wifiHelper.currentConnectedSsid,
                                        signalBars    = wifiHelper.currentSignalBars,
                                    )
                                    Spacer(Modifier.height(10.dp))
                                    AvailableNetworksList(
                                        networks         = networks,
                                        isScanning       = isScanning,
                                        connectingToSsid = connectingToSsid,
                                        onSelectNetwork  = { network ->
                                            selectedNetwork      = network
                                            isPasswordDialogOpen = true
                                        },
                                        modifier = Modifier.fillMaxWidth().weight(1f),
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ── Password dialog ───────────────────────────────────────────────────────
    if (isPasswordDialogOpen && selectedNetwork != null) {
        WifiPasswordDialog(
            network      = selectedNetwork!!,
            isConnecting = connectingToSsid == selectedNetwork!!.ssid,
            onDismiss    = { isPasswordDialogOpen = false },
            onConnect    = { password ->
                val net = selectedNetwork!!
                connectingToSsid  = net.ssid
                statusMessage     = "Connecting to ${net.ssid}…"
                isPasswordDialogOpen = false
                wifiHelper.connectToNetwork(
                    ssid     = net.ssid,
                    password = password.ifBlank { null },
                    isWpa3   = net.isWpa3,
                ) { success, msg ->
                    connectingToSsid = null
                    statusMessage    = msg
                    if (success) {
                        scope.launch {
                            delay(3_000)
                            statusMessage = null
                        }
                    }
                }
            },
        )
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Header bar
// ─────────────────────────────────────────────────────────────────────────────

@Composable
private fun WifiHeaderBar(
    isWifiOn: Boolean,
    isScanning: Boolean,
    scanCooldownSec: Int,
    isConnected: Boolean,
    signalBars: Int,
    onToggleWifi: (Boolean) -> Unit,
    onRefresh: () -> Unit,
    onClose: () -> Unit,
) {
    val infiniteTransition = rememberInfiniteTransition(label = "scanRotate")
    val rotation by infiniteTransition.animateFloat(
        initialValue  = 0f,
        targetValue   = 360f,
        animationSpec = infiniteRepeatable(tween(900, easing = LinearEasing), RepeatMode.Restart),
        label = "scanRotate",
    )

    Row(
        verticalAlignment   = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
        modifier = Modifier.fillMaxWidth(),
    ) {
        // Left: icon + title
        Row(
            verticalAlignment   = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            WifiSignalIcon(isConnected = isConnected && isWifiOn, signalBars = signalBars, size = 26.dp)
            Column {
                Text("Wi-Fi Networks", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                if (isConnected && isWifiOn) {
                    Text("Connected", color = ElectricGreen, fontSize = 11.sp)
                }
            }
        }

        // Right: toggle + scan + close
        Row(
            verticalAlignment   = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Wi-Fi ON/OFF
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                Text(
                    text       = if (isWifiOn) "ON" else "OFF",
                    color      = if (isWifiOn) ElectricGreen else TextDim,
                    fontSize   = 11.sp,
                    fontWeight = FontWeight.Bold,
                )
                Switch(
                    checked         = isWifiOn,
                    onCheckedChange = onToggleWifi,
                    colors = SwitchDefaults.colors(
                        checkedThumbColor   = Color.Black,
                        checkedTrackColor   = ElectricGreen,
                        uncheckedThumbColor = Color.LightGray,
                        uncheckedTrackColor = Color(0xFF222222),
                    ),
                )
            }

            // Scan button — cooldown-aware
            val scanLabel = if (scanCooldownSec > 0) "Wait ${scanCooldownSec}s" else "Scan"
            TvFocusableIconButton(
                label   = scanLabel,
                icon    = Icons.Rounded.Refresh,
                onClick = onRefresh,
                enabled = isWifiOn && scanCooldownSec == 0,
                iconModifier = if (isScanning && isWifiOn) Modifier.rotate(rotation) else Modifier,
            )

            // Close
            TvFocusableButton(text = "✕", onClick = onClose)
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Status cards
// ─────────────────────────────────────────────────────────────────────────────

@Composable
private fun CurrentConnectionStatusCard(
    connectedSsid: String?,
    signalBars: Int,
    modifier: Modifier = Modifier,
) {
    val isConnected = !connectedSsid.isNullOrBlank()
    Surface(
        shape   = RoundedCornerShape(14.dp),
        color   = DarkCard,
        border  = BorderStroke(1.dp, DarkBorder),
        modifier = modifier,
    ) {
        Column(
            modifier = Modifier.fillMaxSize().padding(16.dp),
            verticalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("CURRENT NETWORK", color = TextDim, fontSize = 10.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 1.2.sp)

                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    WifiSignalIcon(isConnected = isConnected, signalBars = signalBars, size = 24.dp)
                    Text(
                        text       = if (isConnected) "Connected" else "Offline",
                        color      = if (isConnected) ElectricGreen else CrimsonRed,
                        fontSize   = 15.sp,
                        fontWeight = FontWeight.Bold,
                    )
                }

                if (isConnected) {
                    Text(connectedSsid!!, color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)

                    // Signal bar visualiser
                    Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                        val barColor = when {
                            signalBars >= 3 -> ElectricGreen
                            signalBars == 2 -> AmberWarn
                            else -> OrangeWeak
                        }
                        repeat(4) { i ->
                            Surface(
                                shape = RoundedCornerShape(2.dp),
                                color = if (i < signalBars) barColor else DarkBorder,
                                modifier = Modifier.width(8.dp).height((6 + i * 4).dp),
                            ) {}
                        }
                    }
                } else {
                    Text(
                        "Select a network below to connect this signage screen.",
                        color = TextMuted, fontSize = 12.sp, lineHeight = 17.sp,
                    )
                }
            }

            Surface(
                shape   = RoundedCornerShape(8.dp),
                color   = Color(0xFF0A0A0A),
                border  = BorderStroke(1.dp, DarkBorder),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text     = "💡 Tap a network to connect.\nKeyboard opens automatically.",
                    color    = TextMuted,
                    fontSize = 11.sp,
                    modifier = Modifier.padding(10.dp),
                    lineHeight = 16.sp,
                )
            }
        }
    }
}

@Composable
private fun CurrentConnectionStatusPill(connectedSsid: String?, signalBars: Int) {
    val isConnected = !connectedSsid.isNullOrBlank()
    Surface(
        shape  = RoundedCornerShape(10.dp),
        color  = DarkCard,
        border = BorderStroke(1.dp, DarkBorder),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            verticalAlignment   = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                WifiSignalIcon(isConnected = isConnected, signalBars = signalBars, size = 18.dp)
                Text(
                    text       = if (isConnected) connectedSsid!! else "Not Connected",
                    color      = if (isConnected) Color.White else CrimsonRed,
                    fontSize   = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            if (isConnected) {
                Text("✓ Online", color = ElectricGreen, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Empty state when Wi-Fi is OFF
// ─────────────────────────────────────────────────────────────────────────────

@Composable
private fun WifiDisabledEmptyState(onEnableWifi: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(Icons.Rounded.WifiOff, contentDescription = null, tint = CrimsonRed, modifier = Modifier.size(52.dp))
        Spacer(Modifier.height(14.dp))
        Text("Wi-Fi is Off", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        Text(
            "Turn Wi-Fi on to discover wireless networks and reconnect this screen.",
            color = TextMuted, fontSize = 13.sp, textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(max = 360.dp),
        )
        Spacer(Modifier.height(20.dp))
        Button(
            onClick = onEnableWifi,
            colors = ButtonDefaults.buttonColors(containerColor = ElectricGreen),
            shape = RoundedCornerShape(10.dp),
        ) {
            Text("Turn Wi-Fi ON", color = Color.Black, fontWeight = FontWeight.Bold)
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Network list
// ─────────────────────────────────────────────────────────────────────────────

@Composable
private fun AvailableNetworksList(
    networks: List<WifiNetworkItem>,
    isScanning: Boolean,
    connectingToSsid: String?,
    onSelectNetwork: (WifiNetworkItem) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (networks.isEmpty()) {
        Box(modifier = modifier, contentAlignment = Alignment.Center) {
            if (isScanning) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    CircularProgressIndicator(color = ElectricGreen, modifier = Modifier.size(28.dp), strokeWidth = 2.5.dp)
                    Text("Scanning for networks…", color = TextMuted, fontSize = 13.sp)
                }
            } else {
                Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.Rounded.SignalWifi0Bar, contentDescription = null, tint = TextDim, modifier = Modifier.size(36.dp))
                    Text("No networks found.", color = TextMuted, fontSize = 13.sp, textAlign = TextAlign.Center)
                    Text("Tap Scan to search again.", color = TextDim, fontSize = 12.sp)
                }
            }
        }
        return
    }

    LazyColumn(
        modifier              = modifier,
        verticalArrangement   = Arrangement.spacedBy(7.dp),
        contentPadding        = PaddingValues(vertical = 2.dp),
    ) {
        itemsIndexed(networks, key = { _, item -> item.ssid }) { _, network ->
            WifiNetworkCard(
                network          = network,
                isConnecting     = connectingToSsid == network.ssid,
                onClick          = { onSelectNetwork(network) },
            )
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Network card
// ─────────────────────────────────────────────────────────────────────────────

@Composable
private fun WifiNetworkCard(
    network: WifiNetworkItem,
    isConnecting: Boolean,
    onClick: () -> Unit,
) {
    var isFocused by remember { mutableStateOf(false) }
    val interactionSource = remember { MutableInteractionSource() }

    val scale by animateFloatAsState(
        targetValue = when {
            isConnecting -> 0.98f
            isFocused    -> 1.02f
            else         -> 1.0f
        },
        animationSpec = spring(stiffness = Spring.StiffnessMediumLow),
        label = "cardScale",
    )

    Surface(
        shape  = RoundedCornerShape(12.dp),
        color  = if (isFocused) DarkCardFocused else DarkCard,
        border = BorderStroke(
            width = if (isFocused || isConnecting) 1.5.dp else 1.dp,
            color = when {
                isConnecting -> AmberWarn
                isFocused    -> ElectricGreen
                else         -> DarkBorder
            },
        ),
        modifier = Modifier
            .fillMaxWidth()
            .scale(scale)
            .onFocusChanged { isFocused = it.isFocused }
            .focusable(interactionSource = interactionSource)
            .clickable(interactionSource = interactionSource, indication = null, onClick = onClick),
    ) {
        Column {
            Row(
                verticalAlignment   = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
            ) {
                Row(
                    verticalAlignment   = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    WifiSignalIcon(isConnected = true, signalBars = network.signalBars, size = 20.dp)
                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(
                            text       = network.ssid,
                            color      = Color.White,
                            fontSize   = 15.sp,
                            fontWeight = FontWeight.SemiBold,
                            maxLines   = 1,
                            overflow   = TextOverflow.Ellipsis,
                        )
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Icon(
                                imageVector = if (network.isSecured) Icons.Rounded.Lock else Icons.Rounded.LockOpen,
                                contentDescription = null,
                                tint = if (network.isSecured) TextMuted else ElectricGreen,
                                modifier = Modifier.size(11.dp),
                            )
                            Text(
                                text  = buildString {
                                    append(if (network.isSecured) if (network.isWpa3) "WPA3" else "WPA2" else "Open")
                                    append("  ·  ${network.signalPercentage}%")
                                },
                                color = TextMuted, fontSize = 11.sp,
                            )
                        }
                    }
                }

                // Right badge
                AnimatedContent(
                    targetState = isConnecting,
                    transitionSpec = { fadeIn(tween(150)) togetherWith fadeOut(tween(120)) },
                    label = "connectBadge",
                ) { connecting ->
                    if (connecting) {
                        CircularProgressIndicator(color = AmberWarn, modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    } else if (network.isConnected) {
                        Surface(
                            shape = RoundedCornerShape(8.dp),
                            color = ElectricGreen.copy(0.14f),
                            border = BorderStroke(1.dp, ElectricGreen),
                        ) {
                            Text("✓ Connected", color = ElectricGreen, fontSize = 11.sp, fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp))
                        }
                    } else {
                        Surface(
                            shape  = RoundedCornerShape(8.dp),
                            color  = if (isFocused) ElectricGreen else PureBlack,
                            border = BorderStroke(1.dp, if (isFocused) ElectricGreen else DarkBorder),
                        ) {
                            Text("Connect", color = if (isFocused) Color.Black else Color.White,
                                fontSize = 11.sp, fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp))
                        }
                    }
                }
            }

            // Connecting progress bar under the card row
            AnimatedVisibility(visible = isConnecting) {
                LinearProgressIndicator(
                    modifier = Modifier.fillMaxWidth(),
                    color    = AmberWarn,
                    trackColor = DarkBorder,
                )
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Focusable buttons (TV remote + touch)
// ─────────────────────────────────────────────────────────────────────────────

@Composable
private fun TvFocusableButton(
    text: String,
    onClick: () -> Unit,
    enabled: Boolean = true,
) {
    var isFocused by remember { mutableStateOf(false) }
    val interactionSource = remember { MutableInteractionSource() }

    Surface(
        shape = RoundedCornerShape(8.dp),
        color = when {
            !enabled -> DarkCard.copy(0.4f)
            isFocused -> ElectricGreen
            else -> DarkCard
        },
        border = BorderStroke(1.dp, if (isFocused) Color.White else DarkBorder),
        modifier = Modifier
            .onFocusChanged { isFocused = it.isFocused }
            .focusable(enabled = enabled, interactionSource = interactionSource)
            .clickable(enabled = enabled, interactionSource = interactionSource, indication = null, onClick = onClick),
    ) {
        Text(
            text       = text,
            color      = when { !enabled -> Color.DarkGray; isFocused -> Color.Black; else -> Color.White },
            fontSize   = 13.sp,
            fontWeight = FontWeight.Bold,
            modifier   = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
        )
    }
}

@Composable
private fun TvFocusableIconButton(
    label: String,
    icon: ImageVector,
    onClick: () -> Unit,
    enabled: Boolean = true,
    iconModifier: Modifier = Modifier,
) {
    var isFocused by remember { mutableStateOf(false) }
    val interactionSource = remember { MutableInteractionSource() }

    Surface(
        shape  = RoundedCornerShape(8.dp),
        color  = when { !enabled -> DarkCard.copy(0.4f); isFocused -> ElectricGreen; else -> DarkCard },
        border = BorderStroke(1.dp, if (isFocused) Color.White else DarkBorder),
        modifier = Modifier
            .onFocusChanged { isFocused = it.isFocused }
            .focusable(enabled = enabled, interactionSource = interactionSource)
            .clickable(enabled = enabled, interactionSource = interactionSource, indication = null, onClick = onClick),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(5.dp),
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
        ) {
            Icon(
                imageVector        = icon,
                contentDescription = label,
                tint               = when { !enabled -> Color.DarkGray; isFocused -> Color.Black; else -> Color.White },
                modifier           = Modifier.size(15.dp).then(iconModifier),
            )
            Text(
                text       = label,
                color      = when { !enabled -> Color.DarkGray; isFocused -> Color.Black; else -> Color.White },
                fontSize   = 12.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Password dialog — keyboard fixed for TV + touch
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun WifiPasswordDialog(
    network: WifiNetworkItem,
    isConnecting: Boolean,
    onDismiss: () -> Unit,
    onConnect: (password: String) -> Unit,
) {
    var password by remember { mutableStateOf("") }
    var passwordVisible by remember { mutableStateOf(false) }
    val focusRequester = remember { FocusRequester() }
    val keyboardController = LocalSoftwareKeyboardController.current

    // FIX: request focus AND explicitly show IME — both needed for TV.
    LaunchedEffect(Unit) {
        delay(200L)
        focusRequester.requestFocus()
        delay(100L)
        keyboardController?.show()
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties       = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier.fillMaxSize().background(Color.Black.copy(0.88f)),
        ) {
            // Scale-up entrance
            AnimatedVisibility(
                visible = true,
                enter   = scaleIn(spring(dampingRatio = 0.75f, stiffness = 450f), initialScale = 0.88f) + fadeIn(tween(180)),
            ) {
                Surface(
                    shape           = RoundedCornerShape(20.dp),
                    color           = PureBlack,
                    border          = BorderStroke(1.dp, DarkBorder),
                    shadowElevation = 32.dp,
                    modifier        = Modifier.widthIn(max = 460.dp).padding(20.dp),
                ) {
                    Column(
                        modifier              = Modifier.padding(24.dp),
                        horizontalAlignment   = Alignment.CenterHorizontally,
                        verticalArrangement   = Arrangement.spacedBy(16.dp),
                    ) {
                        // Network name + icon
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            WifiSignalIcon(isConnected = true, signalBars = network.signalBars, size = 26.dp)
                            Column {
                                Text(
                                    text       = network.ssid,
                                    color      = Color.White,
                                    fontSize   = 17.sp,
                                    fontWeight = FontWeight.Bold,
                                    maxLines   = 1,
                                    overflow   = TextOverflow.Ellipsis,
                                )
                                Text(
                                    text  = if (network.isWpa3) "WPA3 · Enter password" else "WPA2 · Enter password",
                                    color = TextMuted,
                                    fontSize = 11.sp,
                                )
                            }
                        }

                        // Password field
                        OutlinedTextField(
                            value           = password,
                            onValueChange   = { password = it },
                            label           = { Text("Password", color = TextMuted) },
                            singleLine      = true,
                            visualTransformation = if (passwordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                            keyboardActions = KeyboardActions(onDone = {
                                if (password.isNotBlank() && !isConnecting) onConnect(password)
                            }),
                            trailingIcon = {
                                IconButton(onClick = { passwordVisible = !passwordVisible }) {
                                    Icon(
                                        imageVector = if (passwordVisible) Icons.Rounded.LockOpen else Icons.Rounded.Lock,
                                        contentDescription = if (passwordVisible) "Hide password" else "Show password",
                                        tint = Color.White,
                                        modifier = Modifier.size(18.dp),
                                    )
                                }
                            },
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedBorderColor   = ElectricGreen,
                                unfocusedBorderColor = DarkBorder,
                                focusedTextColor     = Color.White,
                                unfocusedTextColor   = Color.White,
                                focusedContainerColor   = DarkCard,
                                unfocusedContainerColor = DarkCard,
                                cursorColor             = ElectricGreen,
                            ),
                            modifier = Modifier.fillMaxWidth().focusRequester(focusRequester),
                        )

                        // Buttons
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Button(
                                onClick = onDismiss,
                                colors  = ButtonDefaults.buttonColors(containerColor = DarkCard),
                                shape   = RoundedCornerShape(10.dp),
                                border  = BorderStroke(1.dp, DarkBorder),
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("Cancel", color = Color.White)
                            }

                            Button(
                                onClick  = { if (!isConnecting) onConnect(password) },
                                enabled  = password.isNotBlank() && !isConnecting,
                                colors   = ButtonDefaults.buttonColors(
                                    containerColor         = ElectricGreen,
                                    disabledContainerColor = DarkCard,
                                ),
                                shape    = RoundedCornerShape(10.dp),
                                modifier = Modifier.weight(1f),
                            ) {
                                AnimatedContent(
                                    targetState = isConnecting,
                                    transitionSpec = { fadeIn(tween(120)) togetherWith fadeOut(tween(100)) },
                                    label = "connectBtn",
                                ) { connecting ->
                                    if (connecting) {
                                        CircularProgressIndicator(color = Color.Black, modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                                    } else {
                                        Text("Connect", color = Color.Black, fontWeight = FontWeight.Bold)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
