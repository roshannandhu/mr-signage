package com.olrac.signage.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * The two switches this player cannot turn on for itself, asked for once and checked until done.
 *
 * Android lets no app grant itself "Display over other apps" or enable its own accessibility
 * service -- so on a TV that is not device owner these are the operator's two taps, and without
 * them the player cannot come back after a restart or reclaim the screen. Rather than leave that
 * to a support call, the player refuses to go on until both are on: each row opens the exact
 * settings page, the state is re-read every second, and the moment both are on this screen gets
 * out of the way by itself.
 *
 * Not shown on a device-owner install (the tablet), which already holds both powers.
 */
@Composable
fun SetupPermissionsScreen(
    overlayGranted: Boolean,
    watchdogEnabled: Boolean,
    autoUpdateGranted: Boolean = true,
    onEnableOverlay: () -> Unit,
    onEnableWatchdog: () -> Unit,
    onEnableAutoUpdate: () -> Unit = {},
    error: String? = null,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF0B1120))
            .verticalScroll(rememberScrollState())
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "MR SIGNAGE",
            color = AccentGreen,
            fontWeight = FontWeight.Bold,
            fontSize = 20.sp,
        )
        Spacer(modifier = Modifier.height(10.dp))
        Text(
            text = "Three settings to switch on",
            color = Color.White,
            fontSize = 26.sp,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = "This screen needs all three to play ads on its own, come back after a restart, " +
                "and receive background updates. Switch each one on, then this page closes by itself.",
            color = Color.LightGray,
            textAlign = TextAlign.Center,
        )
        Spacer(modifier = Modifier.height(28.dp))

        PermissionRow(
            step = "1",
            title = "Display over other apps",
            detail = "Lets the player put itself back on screen.",
            granted = overlayGranted,
            onEnable = onEnableOverlay,
        )
        Spacer(modifier = Modifier.height(14.dp))
        PermissionRow(
            step = "2",
            title = "Keep player on screen",
            detail = "Under Accessibility, switch on \"MR Signage\". This is what reopens the " +
                "player after the TV restarts.",
            granted = watchdogEnabled,
            onEnable = onEnableWatchdog,
        )
        Spacer(modifier = Modifier.height(14.dp))
        PermissionRow(
            step = "3",
            title = "Allow auto-updates",
            detail = "Allows the TV to install player app updates unattended.",
            granted = autoUpdateGranted,
            onEnable = onEnableAutoUpdate,
        )

        error?.let {
            Spacer(modifier = Modifier.height(14.dp))
            Text(text = it, color = Color(0xFFEF4444), textAlign = TextAlign.Center)
        }

        val allGranted = overlayGranted && watchdogEnabled && autoUpdateGranted
        Spacer(modifier = Modifier.height(24.dp))
        Text(
            text = if (allGranted) {
                "All on — starting the player…"
            } else {
                "Waiting for all settings to be switched on…"
            },
            color = if (allGranted) AccentGreen else Color(0xFFFFC46B),
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun PermissionRow(
    step: String,
    title: String,
    detail: String,
    granted: Boolean,
    onEnable: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color(0xFF162032))
            .padding(18.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = if (granted) "✓" else step,
            color = if (granted) AccentGreen else Color(0xFFFFC46B),
            fontSize = 22.sp,
            fontWeight = FontWeight.Bold,
        )
        Spacer(modifier = Modifier.width(16.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(text = title, color = Color.White, fontWeight = FontWeight.Bold, fontSize = 17.sp)
            Text(text = detail, color = Color.LightGray, fontSize = 13.sp)
        }
        Spacer(modifier = Modifier.width(16.dp))
        if (granted) {
            Text(text = "On", color = AccentGreen, fontWeight = FontWeight.Bold)
        } else {
            Button(
                onClick = onEnable,
                colors = ButtonDefaults.buttonColors(
                    containerColor = AccentGreen, contentColor = Color.Black
                ),
            ) {
                Text("Turn on")
            }
        }
    }
}
