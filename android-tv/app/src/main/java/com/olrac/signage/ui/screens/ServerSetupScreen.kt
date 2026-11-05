package com.olrac.signage.ui.screens

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.olrac.signage.boot.PlayerLauncher
import kotlinx.coroutines.delay

@Composable
fun ServerSetupScreen(
    defaultHome: Boolean,
    onChooseHome: () -> Unit,
    onExit: () -> Unit,
    onUnlink: () -> Unit,
    onClose: () -> Unit
) {
    // Taps and remote presses are ignored for a moment after this screen appears. It opens in
    // place of the PIN prompt, and a second tap on "Unlock" landed on whatever button now sat
    // under the finger: on the Lenovo TB-8505F a double tap 0.3 s apart unlocked AND pressed
    // "Exit to home screen", and the same tap could as easily have hit "Unlink Screen".
    var armed by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        delay(INPUT_ARM_DELAY_MS)
        armed = true
    }
    SetupSurface {
      // Scrolls because the maintenance tools no longer fit a TV at 48dp padding, and an
      // unscrollable column simply drops the last buttons off the bottom edge. D-pad focus
      // brings each control into view as it moves.
      Column(
        modifier = Modifier
            .fillMaxWidth()
            // Consumed in the Initial pass, before any button sees the press.
            .pointerInput(armed) {
                if (!armed) awaitPointerEventScope {
                    while (true) awaitPointerEvent(PointerEventPass.Initial).changes.forEach { it.consume() }
                }
            }
            .onPreviewKeyEvent { !armed }
            .verticalScroll(rememberScrollState()),
        horizontalAlignment = Alignment.CenterHorizontally
      ) {
        Text(text = "Player setup", color = Color.White)
        // No server address here any more. A screen that is set up already talks to its
        // server, and a field that repoints it is the one control on this page that can
        // silently cut a working TV off from its workspace -- one typo and it plays nothing.
        Text(
            text = "Maintenance for this screen.",
            color = Color.LightGray,
            textAlign = TextAlign.Center
        )
        Spacer(modifier = Modifier.height(24.dp))
        MaintenanceControls(defaultHome, onChooseHome)
        Spacer(modifier = Modifier.height(24.dp))
        // Leaves the player for the device's own home screen, with kiosk released so Home and
        // the other apps actually work. Opening MR Signage again puts kiosk back.
        Button(
            onClick = onExit,
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF334155), contentColor = Color.White),
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Exit to home screen")
        }
        Spacer(modifier = Modifier.height(10.dp))
        Button(
            onClick = onUnlink,
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFEF4444), contentColor = Color.White),
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Switch Account / Unlink Screen")
        }
        Spacer(modifier = Modifier.height(10.dp))
        Button(
            onClick = onClose,
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF059669), contentColor = Color.White),
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Lock Screen & Return to Ads")
        }
      }
    }
}

/** Launcher role, system settings and restart behaviour -- everything but the server address. */
@Composable
private fun MaintenanceControls(defaultHome: Boolean, onChooseHome: () -> Unit) {
    val context = LocalContext.current
    Text(
        text = if (defaultHome) {
            "Default TV launcher: enabled"
        } else {
            "Default TV launcher: not enabled — required for reliable reboot recovery"
        },
        color = if (defaultHome) AccentGreen else Color(0xFFFFC46B),
        textAlign = TextAlign.Center
    )
    if (!defaultHome) {
        Spacer(modifier = Modifier.height(10.dp))
        Button(onClick = onChooseHome, colors = secondaryButtonColors()) {
            Text("Choose MR Signage as TV launcher")
        }
    }
    Spacer(modifier = Modifier.height(18.dp))
    Button(
        onClick = {
            try {
                context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            } catch (e: Exception) {}
        },
        colors = secondaryButtonColors()
    ) {
        Text("Open Android System Settings")
    }
    Spacer(modifier = Modifier.height(18.dp))
    StartAfterRestartControls()
}

@Composable
fun ServerControls(
    serverUrl: String,
    serverError: String?,
    defaultHome: Boolean,
    onSave: (String) -> Unit,
    onChooseHome: () -> Unit
) {
    var value by remember(serverUrl) { mutableStateOf(serverUrl) }

    OutlinedTextField(
        value = value,
        onValueChange = { value = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text("Server URL") },
        supportingText = serverError?.let { error -> { Text(error) } },
        isError = serverError != null,
        singleLine = true,
        colors = setupFieldColors()
    )
    Spacer(modifier = Modifier.height(12.dp))
    Button(
        onClick = { onSave(value) },
        modifier = Modifier.fillMaxWidth(),
        colors = ButtonDefaults.buttonColors(containerColor = AccentGreen, contentColor = Color.Black)
    ) {
        Text("Save and reconnect")
    }
    Spacer(modifier = Modifier.height(18.dp))
    Text(
        text = if (defaultHome) {
            "Default TV launcher: enabled"
        } else {
            "Default TV launcher: not enabled — required for reliable reboot recovery"
        },
        color = if (defaultHome) AccentGreen else Color(0xFFFFC46B),
        textAlign = TextAlign.Center
    )
    if (!defaultHome) {
        Spacer(modifier = Modifier.height(10.dp))
        Button(onClick = onChooseHome, colors = secondaryButtonColors()) {
            Text("Choose MR Signage as TV launcher")
        }
    }
    
    val context = LocalContext.current
    Spacer(modifier = Modifier.height(18.dp))
    Button(
        onClick = { 
            val intent = Intent(Settings.ACTION_SETTINGS)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            try { context.startActivity(intent) } catch (e: Exception) {}
        }, 
        colors = secondaryButtonColors()
    ) {
        Text("Open Android System Settings")
    }

}

/**
 * Whether this TV will reopen the player by itself after a restart, and the one switch that
 * makes it do so.
 *
 * On Android 10+ an app may bring itself to the front from the background only as the device
 * owner or with "Display over other apps". The boot receiver runs on every restart either way;
 * without that permission Android silently refuses the launch, so the TV comes back to its own
 * home screen and stays there. An accessibility watchdog used to be the workaround, and Android
 * 13+ locks that switch for any app installed from a file -- which is every TV this is put on.
 * This permission is not under that lock, and needs only the remote.
 */
@Composable
private fun StartAfterRestartControls() {
    val context = LocalContext.current
    var allowed by remember { mutableStateOf(PlayerLauncher.canStartFromBackground(context)) }
    var openError by remember { mutableStateOf<String?>(null) }

    // Re-read while this screen is up: the switch is flipped in Settings, and whoever flips it
    // comes back here expecting to see it confirmed.
    LaunchedEffect(Unit) {
        while (true) {
            delay(2_000)
            allowed = PlayerLauncher.canStartFromBackground(context)
        }
    }

    // TV settings apps differ: try this app's own page, then the full list, then Settings.
    fun openOverlaySettings() {
        val attempts = listOf(
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${context.packageName}")),
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION),
            Intent(Settings.ACTION_SETTINGS),
        )
        openError = if (attempts.any { intent ->
                runCatching { context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess
            }) null else "This TV has no settings screen that can be opened from here."
    }

    if (allowed) {
        Text(
            text = "Opens after restart: yes — the player comes up by itself when the TV restarts",
            color = AccentGreen,
            textAlign = TextAlign.Center
        )
    } else {
        Text(
            text = "Opens after restart: not yet — after a restart this TV stays on its home screen",
            color = Color(0xFFFFC46B),
            textAlign = TextAlign.Center
        )
        // Low-RAM TVs show the overlay switch but never apply it; the launcher button above is
        // the way on those.
        val lowRam = remember { context.getSystemService(android.app.ActivityManager::class.java).isLowRamDevice }
        if (lowRam) {
            Text(
                text = "This TV ignores \"Display over other apps\" from its settings. It can be " +
                    "switched on once from a computer: adb shell appops set com.olrac.signage " +
                    "SYSTEM_ALERT_WINDOW allow",
                color = Color.LightGray,
                textAlign = TextAlign.Center
            )
        } else {
            Text(
                text = "Allow \"Display over other apps\" for MR Signage. If the button lands on a " +
                    "list, choose MR Signage and switch it on. On most TVs it is under Settings → " +
                    "Apps → Special app access → Display over other apps.",
                color = Color.LightGray,
                textAlign = TextAlign.Center
            )
            Spacer(modifier = Modifier.height(10.dp))
            Button(onClick = ::openOverlaySettings, colors = secondaryButtonColors(), modifier = Modifier.fillMaxWidth()) {
                Text("Allow MR Signage to open after restart")
            }
        }
    }
    openError?.let {
        Spacer(modifier = Modifier.height(6.dp))
        Text(text = it, color = Color(0xFFEF4444), textAlign = TextAlign.Center)
    }
}

/** Long enough to swallow a double tap or a held OK, short enough to go unnoticed. */
private const val INPUT_ARM_DELAY_MS = 700L
