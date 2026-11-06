package com.olrac.signage

import android.content.Context
import android.content.ComponentName
import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import com.olrac.signage.receivers.SignageDeviceAdminReceiver
import android.app.role.RoleManager
import androidx.activity.result.contract.ActivityResultContracts
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.olrac.signage.data.AppDatabase
import com.olrac.signage.data.DeviceState
import com.olrac.signage.data.LaunchState
import com.olrac.signage.data.LaunchStateResolver
import com.olrac.signage.data.CornerTapCounter
import com.olrac.signage.data.KeyOutcome
import com.olrac.signage.data.MaintenanceGesture
import com.olrac.signage.data.MaintenanceKeyDispatcher
import com.olrac.signage.data.RegistrationSnapshot
import com.olrac.signage.network.ApiClient
import com.olrac.signage.network.RegisterRequest
import com.olrac.signage.network.GooglePollRequest
import com.olrac.signage.network.GoogleStartRequest
import com.olrac.signage.network.GoogleStartResponse
import com.olrac.signage.network.SignInRequest
import com.olrac.signage.service.PlaybackService
import com.olrac.signage.ui.PlayerScreen
import com.olrac.signage.ui.screens.*
import com.olrac.signage.network.RealtimeClient
import com.olrac.signage.device.DeviceOwnerManager
import com.olrac.signage.boot.SetupGate
import com.olrac.signage.boot.SetupRequirement
import com.olrac.signage.boot.SetupRequirementState
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.coroutines.coroutineContext
import java.io.IOException

class MainActivity : ComponentActivity() {
    private lateinit var deviceState: DeviceState
    private var launchState by mutableStateOf<LaunchState>(LaunchState.CheckingLocalState)
    private var serverRevision by mutableIntStateOf(0)
    private var serverError by mutableStateOf<String?>(null)
    private var showServerSetup by mutableStateOf(false)
    private var showPinPrompt by mutableStateOf(false)
    private var defaultHome by mutableStateOf(false)
    private var pairingJob: Job? = null
    private var showPermissionGate by mutableStateOf(false)
    private var requirementStates by mutableStateOf<List<SetupRequirementState>>(emptyList())
    private var setupError by mutableStateOf<String?>(null)
    private var lastInteractionAtMs by mutableStateOf<Long>(System.currentTimeMillis())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        volumeControlStream = android.media.AudioManager.STREAM_MUSIC

        configurePlayerWindow()
        deviceState = DeviceState(this)
        val deviceId = deviceState.deviceId

        val forceSignIn = LaunchStateResolver.honourForcedSignIn(
            requested = intent?.getBooleanExtra("show_signin", false) ?: false,
            isPaired = deviceState.isPaired,
        )
        if (forceSignIn) {
            deviceState.clearPairing()
            launchState = LaunchState.SignIn()
        } else if (deviceState.isPaired) {
            launchState = LaunchState.Playing(deviceState.screenName)
            PlaybackService.start(this, launchPlayer = false)
        } else {
            launchState = LaunchState.CheckingLocalState
        }
        
        if (isHomePressWhileExited()) {
            // Recreated by a Home press while the operator is outside the player: no kiosk.
            openSystemLauncher()
        } else {
            // Status bar, keyguard, auto-grant and uninstall protection stay on regardless --
            // those guard the DEVICE, not the viewer's ability to leave the player, and
            // kiosk_lock is about the latter. Only the task pin (startLockTask) is gated.
            //
            // Read here and in rearmLockTask, not watched live: a plan change that flips
            // this while a device-owner TV is already pinned takes effect from its next
            // restart, not mid-session. The non-device-owner path (MaintenanceKeyDispatcher)
            // has no such lag -- it reads the same flag fresh on every BACK press.
            DeviceOwnerManager.applyKioskPolicy(this)
            if (DeviceOwnerManager.isDeviceOwner(this) &&
                com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(this)
            ) {
                try {
                    startLockTask()
                } catch (e: Exception) {
                    // Ignore if it fails
                }
            }
        }

        setContent {
            LaunchedEffect(deviceId, serverRevision) {
                if (!forceSignIn) {
                    resolveAndRefreshPairing(deviceId)
                }
            }

            // Re-read the two switches once a second so a row turns green the moment the
            // installer flips it, without them having to come back and forth. The loop ends
            // for good once both are on -- the gate never asks again, and a TV that runs for
            // weeks is not left with a 1 Hz timer for nothing.
            LaunchedEffect(Unit) {
                while (true) {
                    val states = readRequirementStates()
                    requirementStates = states
                    val handledByOwner = DeviceOwnerManager.isDeviceOwner(this@MainActivity)
                    if (handledByOwner || SetupGate.isSatisfied(states)) {
                        showPermissionGate = false
                        return@LaunchedEffect
                    }
                    showPermissionGate = true
                    delay(1_000L)
                }
            }

            // 5-minute inactivity auto-relock when ServerSetupScreen is open (only when kiosk is locked)
            LaunchedEffect(showServerSetup) {
                if (showServerSetup) {
                    while (showServerSetup) {
                        if (!com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(this@MainActivity)) {
                            // Unlocked state: stay unlocked indefinitely, do not force-relock
                            break
                        }
                        val elapsed = System.currentTimeMillis() - lastInteractionAtMs
                        if (elapsed >= 5 * 60_000L) {
                            android.util.Log.i("MainActivity", "ServerSetupScreen 5-minute inactivity timeout reached; relocking to player")
                            showServerSetup = false
                            relockToPlayer()
                            break
                        }
                        delay(10_000L)
                    }
                }
            }

            // Direct reactive reception of remote unlock & commands from PlaybackService (0ms latency)
            LaunchedEffect(Unit) {
                com.olrac.signage.service.PlaybackService.commandEvents.collect { event ->
                    when (event) {
                        is com.olrac.signage.service.ServiceCommandEvent.RemoteUnlock -> {
                            if (!showPinPrompt && !showServerSetup) {
                                android.util.Log.i("MainActivity", "Applying reactive remote unlock (id=${event.commandId})")
                                setKioskLockState(locked = false, reportToServer = false)
                                enterMaintenance()
                                showServerSetup = true
                            }
                        }
                        is com.olrac.signage.service.ServiceCommandEvent.KioskLockChanged -> {
                            setKioskLockState(event.locked, reportToServer = false)
                        }
                    }
                }
            }

            // Runs for the whole life of the Activity, unlike the gate loop above: a remote
            // unlock can arrive at any time, not only during setup. Checked here rather than
            // only in onResume because a device-owner TV resuming into lock task does not
            // always re-run onResume the instant PlaybackService sets the flag.
            LaunchedEffect(Unit) {
                val prefs = getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
                while (true) {
                    if (prefs.getBoolean(PREF_PENDING_REMOTE_UNLOCK, false)) {
                        prefs.edit().putBoolean(PREF_PENDING_REMOTE_UNLOCK, false).apply()
                        if (!showPinPrompt && !showServerSetup) {
                            android.util.Log.i("MainActivity", "Applying a remote unlock from prefs fallback")
                            setKioskLockState(locked = false, reportToServer = false)
                            enterMaintenance()
                            showServerSetup = true
                        }
                    }
                    delay(2_000L)
                }
            }

            if (showPinPrompt) {
                PinPromptScreen(
                    expectedPin = deviceState.maintenancePin,
                    masterPin = deviceState.masterPin,
                    onUnlocked = {
                        showPinPrompt = false
                        // Correct pin: unlock kiosk and notify backend so dashboard reflects Unlocked immediately
                        setKioskLockState(locked = false, reportToServer = true)
                        enterMaintenance()
                        showServerSetup = true
                    },
                    onCancel = {
                        showPinPrompt = false
                        // Nothing was unpinned (the gesture no longer does that), but re-arm
                        // defensively in case the lock was dropped by an earlier path.
                        relockToPlayer()
                    }
                )
            } else if (showServerSetup) {
                ServerSetupScreen(
                    defaultHome = defaultHome,
                    onChooseHome = ::requestHomeRole,
                    onExit = ::exitToSystemLauncher,
                    onUnlink = {
                        showServerSetup = false
                        com.olrac.signage.boot.PlayerLauncher.handleUnpairedOrDeleted(this@MainActivity)
                    },
                    onClose = {
                        showServerSetup = false
                        relockToPlayer()
                    }
                )
            } else if (showPermissionGate) {
                // Below the pin prompt and the setup screen on purpose: the maintenance
                // gesture has to stay reachable from the gate, or a TV with both switches
                // off could never be serviced.
                PermissionGateScreen(
                    states = requirementStates,
                    onTurnOn = ::openRequirementSettings
                )
            } else {
                when (val state = launchState) {
                    // Also the state shown while the boot reconnect is retrying, so the
                    // wording has to hold for a TV that cannot reach the server yet. Saying
                    // it is reconnecting is what stops an installer typing in a pairing code
                    // for a screen that is already claimed and simply offline.
                    LaunchState.CheckingLocalState -> BrandedMessage(
                        title = "MR Signage",
                        detail = "Reconnecting to your workspace..."
                    )

                    is LaunchState.Playing -> PlayerScreen()
                    is LaunchState.SignIn -> {
                        val scope = rememberCoroutineScope()
                        LaunchedEffect(ApiClient.effectiveBaseUrl(this@MainActivity)) {
                            refreshAuthMethods()
                        }
                        SignInScreen(
                            state = state,
                            defaultScreenName = deviceState.hardwareName,
                            onUseGoogle = { screenName ->
                                scope.launch { startGoogleSignIn(deviceId, screenName) }
                            },
                            onUsePairingCode = {
                                startPairingFlow(deviceId)
                            },
                            onOpenSettings = {
                                showPinPrompt = true
                            }
                        )
                    }

                    is LaunchState.GoogleSignIn -> GoogleSignInScreen(
                        state = state,
                        serverUrl = ApiClient.effectiveBaseUrl(this),
                        serverError = serverError,
                        defaultHome = defaultHome,
                        onCancel = { launchState = LaunchState.SignIn() },
                        onSaveServer = ::saveServerUrl,
                        onChooseHome = ::requestHomeRole
                    )

                    is LaunchState.Pairing -> PairingScreen(
                        state = state,
                        onBackToSignIn = ::cancelPairingFlow
                    )
                }
            }
        }
    }

    /**
     * Open the maintenance screen's system screens without leaving kiosk. Called only AFTER a
     * correct maintenance pin.
     *
     * This used to stopLockTask(), and that is what threw operators straight back onto the
     * player after typing the PIN. Whenever the player was running as the home task -- which it
     * is after every restart or Home press, being the persistent HOME activity -- Android ends
     * lock task on it by finishing the whole task ("clear-task-all"), then restarts HOME, which
     * is the player again, freshly created and re-pinned. The maintenance screen was gone before
     * it was drawn. Seen on the Lenovo TB-8505F at 12:40:45: Unlock tapped, task cleared 83ms
     * later, a new MainActivity created and locked.
     *
     * Kiosk now stays on, and the apps the maintenance buttons open (Settings, the permission
     * controller) are added to the lock-task allowlist instead, so they can open inside it.
     * Returning to the player narrows the allowlist back; only Exit ends lock task.
     */
    /**
     * Back on the ads from a maintenance surface: re-lock at once.
     *
     * The maintenance window and any exit are ended here, not left to expire, so the watchdog
     * protects the screen again and the very next attempt to leave needs the PIN. Without this,
     * "Return to player" left the suppress set at PIN time running for its full 15 minutes, and
     * an operator could reach the home screen the whole time without re-entering the PIN.
     */
    private fun relockToPlayer() {
        setKioskLockState(locked = true, reportToServer = true)
    }

    private fun refreshPermissionState() {
        val states = readRequirementStates()
        requirementStates = states
        if (SetupGate.isSatisfied(states)) {
            showPermissionGate = false
            getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).edit()
                .remove(PREF_SETUP_AWAITING_UNTIL).apply()
        }
    }

    /** Reads the switches the gate watches. */
    private fun readRequirementStates(): List<SetupRequirementState> =
        SetupGate.ORDER.map { requirement ->
            val granted = when (requirement) {
                SetupRequirement.OVERLAY ->
                    Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)
                SetupRequirement.WATCHDOG ->
                    com.olrac.signage.boot.WatchdogAccessibilityService.isEnabled(this)
                SetupRequirement.AUTO_UPDATE ->
                    Build.VERSION.SDK_INT < Build.VERSION_CODES.O || packageManager.canRequestPackageInstalls()
            }
            SetupRequirementState(requirement, granted)
        }

    /**
     * Opens the exact settings page for one switch. Each has fallbacks because TV builds
     * differ on which of these pages they ship: a KONKA panel is not a Pixel, and an
     * ActivityNotFoundException here would strand the installer on a screen whose only
     * button does nothing.
     */
    private fun openRequirementSettings(requirement: SetupRequirement) {
        val candidates = when (requirement) {
            SetupRequirement.OVERLAY -> listOf(
                Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")),
                Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION),
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")),
                Intent(Settings.ACTION_SETTINGS),
            )
            SetupRequirement.WATCHDOG -> listOf(
                Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS),
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")),
                Intent(Settings.ACTION_SETTINGS),
            )
            SetupRequirement.AUTO_UPDATE -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    listOf(
                        Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")),
                        Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES),
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")),
                        Intent(Settings.ACTION_SECURITY_SETTINGS),
                        Intent(Settings.ACTION_SETTINGS),
                    )
                } else {
                    listOf(
                        Intent(Settings.ACTION_SECURITY_SETTINGS),
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")),
                        Intent(Settings.ACTION_SETTINGS),
                    )
                }
            }
        }
        openSetupSettings(candidates)
    }

    /**
     * Open a settings page for one of the switches.
     *
     * Leaves the operator in Settings until they finish toggling and press Back on the remote.
     * Kiosk pinning is suppressed so Settings can open, and Watchdog is stood down so it does
     * not interrupt them while configuring.
     */
    private fun openSetupSettings(attempts: List<Intent>) {
        setupError = null
        // Stand every relaunch path down while they are in Settings. Writing this flag was not
        // enough on its own -- nothing read it, so PlaybackService's boot follow-up kept pulling
        // the player back over Settings a few seconds after the gate opened it.
        com.olrac.signage.boot.PlayerLauncher.holdForSetup(this, SETUP_RETURN_WINDOW_MS)
        // Kiosk pinning would block Settings from opening.
        enterMaintenance()
        val opened = attempts.any { intent ->
            runCatching { startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess
        }
        if (!opened) {
            setupError = "This TV has no settings screen that can be opened from here."
            return
        }
    }

    private fun enterMaintenance() {
        lastInteractionAtMs = System.currentTimeMillis()
        getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).edit()
            .putLong(PREF_WATCHDOG_SUPPRESS_UNTIL, Long.MAX_VALUE).apply()
        DeviceOwnerManager.allowMaintenanceApps(this)
    }

    /**
     * Leave the player for the device's own home screen, reachable only from the maintenance
     * screen, i.e. after the PIN.
     *
     * Three things kept an operator in, and all three have to go: lock task (other apps cannot
     * start), the persistent preferred HOME activity (every Home press reopened the player) and
     * the disabled status bar. They come back through onResume the next time the player is in
     * front -- opened again from the launcher, a remote "Open app on TV", or a restart.
     *
     * The task is left alone rather than finished: removing it fires PlaybackService's
     * onTaskRemoved, which relaunches the player by design, and would undo the exit at once.
     * The exit time is recorded so that relaunch also stands down if someone swipes it away.
     */
    private fun exitToSystemLauncher() {
        showServerSetup = false
        setKioskLockState(locked = false, reportToServer = true)
        val launcher = systemLauncher()
        val opened = openSystemLauncher(launcher)
        android.util.Log.i("MainActivity", "Operator exit to ${launcher?.packageName ?: "background"} (opened=$opened)")
    }

    private fun systemLauncher(): com.olrac.signage.data.SystemLauncherPicker.Candidate? {
        val home = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        val candidates = packageManager.queryIntentActivities(home, 0).map {
            com.olrac.signage.data.SystemLauncherPicker.Candidate(it.activityInfo.packageName, it.activityInfo.name)
        }
        return com.olrac.signage.data.SystemLauncherPicker.pick(candidates, packageName)
    }

    private fun openSystemLauncher(launcher: com.olrac.signage.data.SystemLauncherPicker.Candidate? = systemLauncher()): Boolean {
        val opened = launcher != null && runCatching {
            startActivity(
                Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
                    .setClassName(launcher.packageName, launcher.className)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }.isSuccess
        if (!opened) moveTaskToBack(true)
        return opened
    }

    /** Whether the operator has left the player and not deliberately come back to it yet. */
    private fun operatorExited(): Boolean =
        getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).contains(PREF_OPERATOR_EXIT_AT)

    /**
     * A Home press that arrived here while the operator is outside the player.
     *
     * Redirecting Home to the device launcher is not enough on its own: on the test tablet Home
     * still resolved to this player after an exit (it also holds the HOME role), so three presses
     * pulled the player back and re-locked it. While exited, a HOME intent is passed straight on
     * to the launcher. Coming back is a deliberate act -- the app icon, "Open app on TV" or a
     * restart -- none of which arrives as HOME.
     */
    private fun isHomePressWhileExited(): Boolean =
        operatorExited() && intent?.hasCategory(Intent.CATEGORY_HOME) == true

    /** Re-pin the kiosk once no maintenance surface is open. Safe to call when already
     *  pinned (a no-op). This is what closes the hole where the gesture alone, or a
     *  cancelled/failed pin, left the TV un-pinned until the next reboot. */
    private fun rearmLockTask() {
        if (!showPinPrompt && !showServerSetup &&
            com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(this)
        ) {
            try {
                if (DeviceOwnerManager.isDeviceOwner(this)) {
                    val dpm = getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
                    val adminName = ComponentName(this, SignageDeviceAdminReceiver::class.java)
                    if (!dpm.isLockTaskPermitted(packageName)) {
                        dpm.setLockTaskPackages(adminName, arrayOf(packageName))
                    }
                }
                val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
                if (am.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE) {
                    startLockTask()
                    android.util.Log.i("MainActivity", "startLockTask successfully engaged (state=LOCKED)")
                }
            } catch (e: Exception) {
                android.util.Log.w("MainActivity", "startLockTask failed", e)
            }
        }
    }

    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        if (com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(this) &&
            !operatorExited() && !showPinPrompt && !showServerSetup
        ) {
            android.util.Log.i("MainActivity", "onUserLeaveHint intercepted; re-asserting player front")
            val intent = Intent(this, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            }
            startActivity(intent)
        }
    }

    fun setKioskLockState(locked: Boolean, reportToServer: Boolean = true) {
        val prefs = getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
        prefs.edit().putBoolean("kiosk_lock", locked).apply()

        runOnUiThread {
            if (locked) {
                prefs.edit().remove(PREF_OPERATOR_EXIT_AT).remove(PREF_WATCHDOG_SUPPRESS_UNTIL).apply()
                DeviceOwnerManager.applyKioskPolicy(this)
                rearmLockTask()
                android.util.Log.i("MainActivity", "Kiosk Lock re-armed (locked=true)")
            } else {
                prefs.edit().putLong(PREF_OPERATOR_EXIT_AT, System.currentTimeMillis())
                    .putLong(PREF_WATCHDOG_SUPPRESS_UNTIL, Long.MAX_VALUE).apply()
                val launcher = systemLauncher()
                DeviceOwnerManager.releaseKioskPolicy(this, launcher?.let { ComponentName(it.packageName, it.className) })
                try {
                    val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
                    if (am.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE) {
                        stopLockTask()
                    }
                } catch (e: Exception) {
                    android.util.Log.w("MainActivity", "stopLockTask failed: ${e.message}")
                }
                showPinPrompt = false
                android.util.Log.i("MainActivity", "Kiosk Lock released (locked=false)")
            }
        }

        if (reportToServer) {
            val deviceId = deviceState.deviceId
            @Suppress("OPT_IN_USAGE")
            kotlinx.coroutines.GlobalScope.launch(kotlinx.coroutines.Dispatchers.IO) {
                try {
                    ApiClient.service(this@MainActivity).reportKioskStatus(
                        deviceId = deviceId,
                        request = com.olrac.signage.network.KioskStatusUpdateRequest(locked = locked)
                    )
                    android.util.Log.i("MainActivity", "Successfully reported kiosk status to backend (locked=$locked)")
                } catch (e: Exception) {
                    android.util.Log.w("MainActivity", "Could not report kiosk status: ${e.message}")
                }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        visible = true
        activeActivity = this
    }

    override fun onStop() {
        if (activeActivity == this) activeActivity = null
        visible = false
        super.onStop()
    }

    override fun onDestroy() {
        if (activeActivity == this) activeActivity = null
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        // With BootReceiver's timestamp, tells a remote report whether a restart reopened the player.
        getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).edit()
            .putLong(PREF_PLAYER_RESUMED_AT, System.currentTimeMillis()).apply()
        if (launchState is LaunchState.SignIn && !deviceState.isPaired) {
            launchState = LaunchState.SignIn(busy = false)
        }
        if (isHomePressWhileExited()) {
            openSystemLauncher()
            return
        }
        // Back in front after an operator exit: kiosk returns with the player. Not while the PIN
        // prompt or the maintenance screen is open -- re-applying there would shrink the lock-task
        // allowlist under Settings the operator just opened from it.
        if (!showPinPrompt && !showServerSetup) {
            if (com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(this)) {
                // Deliberately back on the player: end the exit AND let the watchdog protect again.
                getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).edit()
                    .remove(PREF_OPERATOR_EXIT_AT).remove(PREF_WATCHDOG_SUPPRESS_UNTIL).apply()
                DeviceOwnerManager.applyKioskPolicy(this)
                rearmLockTask()
            } else {
                val launcher = systemLauncher()
                DeviceOwnerManager.releaseKioskPolicy(this, launcher?.let { ComponentName(it.packageName, it.className) })
                try { stopLockTask() } catch (e: Exception) {}
            }
        }
        defaultHome = isDefaultHomeLauncher()
        // The player is in front again, so the setup hold has done its job. Lifting it here
        // rather than letting it expire means full protection resumes the moment they return,
        // instead of staying off for the rest of the five-minute window.
        com.olrac.signage.boot.PlayerLauncher.releaseSetupHold(this)
        // Back from a settings page: pick up a switch that was just turned on straight away.
        refreshPermissionState()
        askOnceToBecomeHome()
        hideSystemBars()
        // Backstop: re-pin whenever we are back on the player with no maintenance surface
        // open -- covers a cancelled pin, a wrong pin, and returning from the system
        // launcher-role chooser.
        rearmLockTask()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    private val maintenanceKeys = MaintenanceKeyDispatcher()
    private val cornerTaps = CornerTapCounter()
    private val homePressTimes = ArrayDeque<Long>()

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)

        if (LaunchStateResolver.honourForcedSignIn(
                requested = intent?.getBooleanExtra("show_signin", false) == true,
                isPaired = deviceState.isPaired,
            )
        ) {
            deviceState.clearPairing()
            launchState = LaunchState.SignIn()
            return
        }

        // Handle Google OAuth Deep Link Return
        val data = intent?.data
        if (data != null && data.scheme == "olrac" && data.host == "auth") {
            val screenName = data.getQueryParameter("screen_name")
            val screenId = data.getQueryParameter("screen_id")
            if (data.path == "/success" || screenId != null) {
                completePairing(screenName ?: deviceState.hardwareName, pairCode = null)
                return
            } else if (data.path == "/failed") {
                val errorMsg = data.getQueryParameter("error") ?: "Google authentication failed"
                launchState = LaunchState.SignIn(error = errorMsg)
                return
            }
        }

        // If the user presses the HOME button and this app is the default launcher, 
        // the OS routes the intent here instead of onKeyDown. 
        // We detect 3 presses within 3 seconds to trigger the PIN prompt.
        if (intent?.hasCategory(Intent.CATEGORY_HOME) == true && !operatorExited()) {
            val now = System.currentTimeMillis()
            homePressTimes.addLast(now)
            while (homePressTimes.isNotEmpty() && now - homePressTimes.first() > 3000L) {
                homePressTimes.removeFirst()
            }
            if (homePressTimes.size >= 3) {
                homePressTimes.clear()
                // Reveal the PIN prompt only; lock-task is dropped after a correct pin
                // (onUnlocked), not here -- otherwise three HOME presses defeat the kiosk.
                showPinPrompt = true
            }
        }
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        lastInteractionAtMs = System.currentTimeMillis()
        // Touch-only path to the maintenance PIN, for devices with no D-pad remote (touch
        // panels, phones) where the Up-Up-Down-Down-OK gesture is unreachable: seven quick
        // taps in any corner reveal the same PIN prompt. Only over the player, and the pin
        // behind it is still the real control.
        if (ev.action == MotionEvent.ACTION_DOWN && !showPinPrompt && !showServerSetup) {
            val inCorner = CornerTapCounter.isCorner(ev.rawX, ev.rawY, window.decorView.width, window.decorView.height)
            if (inCorner) {
                if (cornerTaps.record(System.currentTimeMillis())) {
                    showPinPrompt = true
                    return true
                }
            } else {
                cornerTaps.reset()
            }
        }
        return super.dispatchTouchEvent(ev)
    }

    /**
     * The maintenance gesture, matched before the focused view can act on the key.
     *
     * [onKeyDown] only runs for keys nothing in the view tree consumed, and a focused Button
     * consumes OK. Over the player that never mattered -- there is nothing focusable behind a
     * video -- but on the setup gate the final OK of Up-Up-Down-Down-OK went to whichever
     * button held focus and opened Settings instead of the pin prompt. That left a TV whose
     * switches are both off with no way in at all, so the match has to happen here, ahead of
     * the buttons.
     *
     * Only the press that COMPLETES the sequence is consumed. The four before it fall
     * through untouched, so the arrow keys still move focus around the gate as normal, and
     * the gesture is recorded here ONLY -- recording in onKeyDown as well would count every
     * press twice and the sequence would never match.
     */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        lastInteractionAtMs = System.currentTimeMillis()
        // Not on the pin prompt or the setup screen: there these keys are navigation, and
        // matching would swallow a press mid-form.
        val gestureEnabled = !showPinPrompt && !showServerSetup
        val surface = when {
            showPinPrompt -> com.olrac.signage.data.KeySurface.PIN_PROMPT
            showServerSetup -> com.olrac.signage.data.KeySurface.SERVER_SETUP
            else -> com.olrac.signage.data.KeySurface.PLAYER
        }
        val outcome = maintenanceKeys.onKeyEvent(
            action = event.action,
            keyCode = event.keyCode,
            repeatCount = event.repeatCount,
            gestureEnabled = gestureEnabled,
            nowMs = System.currentTimeMillis(),
            surface = surface,
            kioskLocked = com.olrac.signage.sync.PlaylistSynchronizer.kioskLockEnabled(this)
        )
        return when (outcome) {
            KeyOutcome.REVEAL_PIN -> {
                // Reveal the PIN prompt only. The kiosk stays pinned until a CORRECT pin is
                // entered (see onUnlocked). Dropping lock-task here let the gesture alone
                // un-pin the TV, and cancelling then left it open until the next reboot.
                showPinPrompt = true
                true
            }
            KeyOutcome.CANCEL_PIN -> {
                showPinPrompt = false
                rearmLockTask()
                true
            }
            KeyOutcome.CONSUME -> true
            KeyOutcome.PASS_THROUGH -> super.dispatchKeyEvent(event)
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        when (keyCode) {
            KeyEvent.KEYCODE_VOLUME_DOWN -> {
                val audioManager = getSystemService(android.content.Context.AUDIO_SERVICE) as? android.media.AudioManager
                audioManager?.adjustStreamVolume(
                    android.media.AudioManager.STREAM_MUSIC,
                    android.media.AudioManager.ADJUST_LOWER,
                    android.media.AudioManager.FLAG_SHOW_UI
                )
                return true
            }
            KeyEvent.KEYCODE_VOLUME_UP -> {
                val audioManager = getSystemService(android.content.Context.AUDIO_SERVICE) as? android.media.AudioManager
                audioManager?.adjustStreamVolume(
                    android.media.AudioManager.STREAM_MUSIC,
                    android.media.AudioManager.ADJUST_RAISE,
                    android.media.AudioManager.FLAG_SHOW_UI
                )
                return true
            }
            KeyEvent.KEYCODE_VOLUME_MUTE, KeyEvent.KEYCODE_MUTE -> {
                val audioManager = getSystemService(android.content.Context.AUDIO_SERVICE) as? android.media.AudioManager
                audioManager?.adjustStreamVolume(
                    android.media.AudioManager.STREAM_MUSIC,
                    android.media.AudioManager.ADJUST_TOGGLE_MUTE,
                    android.media.AudioManager.FLAG_SHOW_UI
                )
                return true
            }
        }

        if (keyCode == KeyEvent.KEYCODE_HOME || keyCode == KeyEvent.KEYCODE_APP_SWITCH) {
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    private suspend fun resolveAndRefreshPairing(deviceId: String) {
        val hasLocalPlaylist = AppDatabase.getDatabase(this).playlistDao().hasItems()
        val localState = LaunchStateResolver.resolveLocal(
            isPaired = deviceState.isPaired,
            hasLocalPlaylist = hasLocalPlaylist
        )

        if (localState is LaunchState.Playing) {
            deviceState.markPaired(deviceState.screenName ?: DeviceState.DEFAULT_SCREEN_NAME)
            launchState = LaunchState.Playing(deviceState.screenName)
            PlaybackService.requestImmediateSync(this)

            val refreshed = LaunchStateResolver.refresh(localState) { register(deviceId) }
            if (refreshed is LaunchState.Playing && !refreshed.screenName.isNullOrBlank()) {
                deviceState.markPaired(refreshed.screenName)
            }
            return
        }

        // Keep asking until the server ANSWERS. Failing to reach it is not a statement about
        // whether this screen is claimed, and treating it as one is what stranded panels on
        // the pairing screen: this ran once, and any exception -- a boot that beats Wi-Fi
        // association after a power cut, a cold backend taking longer than the timeout, a
        // momentary DNS failure -- dropped a perfectly well-claimed screen onto the sign-in
        // form, where nothing ever retried. Someone had to walk up to it.
        //
        // Only the server saying "waiting_pairing" ends this loop at the sign-in screen,
        // because that is the one answer that actually means "nobody owns this TV".
        var backoffMs = RECONNECT_FIRST_RETRY_MS
        while (true) {
            val registration = try { register(deviceId) } catch (_: Exception) { null }

            if (registration != null) {
                if (registration.status != LaunchStateResolver.WAITING_PAIRING) {
                    // Recognised -- by device id, or by hardware identity after a reinstall.
                    completePairing(registration.screenName, pairCode = null)
                } else {
                    launchState = LaunchState.SignIn()
                }
                return
            }

            // Unreachable. Say so rather than showing a pairing code the operator might
            // start typing, and try again shortly.
            launchState = LaunchState.CheckingLocalState
            delay(backoffMs)
            backoffMs = (backoffMs * 2).coerceAtMost(RECONNECT_MAX_RETRY_MS)
        }
    }

    /**
     * Bind this screen using the operator's own account. The password is passed straight
     * to the call and never persisted — only the fact that the screen is now claimed is.
     */
    private suspend fun submitSignIn(
        deviceId: String,
        username: String,
        password: String,
        screenName: String
    ) {
        launchState = LaunchState.SignIn(busy = true)
        val response = try {
            ApiClient.service(this).signIn(
                SignInRequest(
                    username = username.trim(),
                    password = password,
                    device_id = deviceId,
                    name = screenName.trim().ifBlank { null },
                    installation_id = deviceState.installationId,
                    model = deviceState.deviceModel,
                    manufacturer = deviceState.manufacturer
                )
            )
        } catch (_: Exception) {
            launchState = LaunchState.SignIn(
                error = "No connection. Check the server address and try again."
            )
            return
        }

        if (response.isSuccessful) {
            completePairing(response.body()?.name, pairCode = null, deviceSecret = response.body()?.device_secret)
            return
        }

        launchState = LaunchState.SignIn(
            error = when (response.code()) {
                401 -> "That username or password is not right."
                403 -> "That account cannot add screens. Use an owner or editor account."
                409 -> "This workspace has reached its screen limit."
                else -> "Sign in failed (error ${response.code()}). Please try again."
            }
        )
    }

    /**
     * Learn which sign-in routes this deployment offers, and hide the ones it does not.
     *
     * Failure is deliberately silent and leaves googleEnabled false: this runs on a TV that
     * may have no network yet, and an error toast about an optional button would be noise
     * on top of the connection problem the installer is already looking at.
     */
    private suspend fun refreshAuthMethods() {
        val enabled = try {
            ApiClient.service(this).authMethods().body()?.google ?: false
        } catch (_: Exception) {
            false
        }
        (launchState as? LaunchState.SignIn)?.let { current ->
            if (current.googleEnabled != enabled) {
                launchState = current.copy(googleEnabled = enabled)
            }
        }
    }

    /**
     * Bind this screen from a Google account, via the device authorisation grant.
     *
     * The TV shows a code and polls; the installer approves on their own phone. No OAuth
     * secret and no Google SDK live here -- the server does both halves of the exchange,
     * which is what lets this work on the AOSP boxes that have no Play Services at all.
     */
    private suspend fun startGoogleSignIn(deviceId: String, screenName: String) {
        launchState = LaunchState.SignIn(busy = true)

        // 1. Fetch direct Google OAuth URL from backend
        val oauthResp = try {
            ApiClient.service(this).getGoogleOAuthUrl(
                deviceId = deviceId,
                name = screenName.trim().ifBlank { null },
                installationId = deviceState.installationId,
                model = deviceState.deviceModel,
                manufacturer = deviceState.manufacturer
            )
        } catch (_: Exception) {
            null
        }

        val baseUrl = ApiClient.effectiveBaseUrl(this)
        val fallbackOAuthUrl = "${baseUrl}api/screens/google/oauth-callback"
        val oauthUrl = oauthResp?.body()?.oauth_url ?: "${baseUrl}api/screens/google/oauth-page?redirect_uri=${Uri.encode(fallbackOAuthUrl)}"

        // Open direct Google OAuth redirect in Browser / Custom Tab
        try {
            val browserIntent = Intent(Intent.ACTION_VIEW, Uri.parse(oauthUrl)).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(browserIntent)
        } catch (e: Exception) {
            try {
                val customTabsIntent = CustomTabsIntent.Builder().build()
                customTabsIntent.launchUrl(this, Uri.parse(oauthUrl))
            } catch (_: Exception) {}
        }

        // 2. Also start background polling so if approved on web or device flow, it auto-transitions
        val started = try {
            ApiClient.service(this).googleStart(
                GoogleStartRequest(
                    device_id = deviceId,
                    name = screenName.trim().ifBlank { null },
                    installation_id = deviceState.installationId,
                    model = deviceState.deviceModel,
                    manufacturer = deviceState.manufacturer
                )
            )
        } catch (_: Exception) {
            null
        }

        val body = started?.body()
        if (body != null) {
            // Check immediate poll for instant 1-tap Google binding
            val initialPoll = try {
                ApiClient.service(this).googlePoll(GooglePollRequest(body.poll_token))
            } catch (_: Exception) {
                null
            }

            if (initialPoll?.isSuccessful == true && initialPoll.body()?.status == "bound") {
                completePairing(
                    initialPoll.body()?.screen?.name,
                    pairCode = null,
                    deviceSecret = initialPoll.body()?.screen?.device_secret,
                )
                return
            }

            launchState = LaunchState.GoogleSignIn(
                userCode = body.user_code,
                verificationUrl = body.verification_url
            )
            pollGoogleSignIn(body)
        } else {
            // The device grant is not configured on this server -- google/start answers 503
            // "Google sign-in is not enabled" -- while the browser opened above IS, and it
            // binds the screen server side.
            //
            // With nothing polling here, the only route back into the app was the olrac://
            // deep link, and a TV browser is free to refuse it ("App deeplink blocked" on
            // TCL). The browser showed "Display Connected" while this dropped straight back
            // to the sign-in form, and the panel stayed there through every relaunch.
            awaitBrowserSignIn(deviceId)
        }
    }

    /**
     * Wait for the browser half to finish, asking OUR server rather than Google.
     *
     * register() reports the binding whoever made it -- the TV's own browser, a phone, or
     * an operator redeeming a pairing code -- so this needs neither a device grant nor a
     * deep link the browser is allowed to block.
     */
    private suspend fun awaitBrowserSignIn(deviceId: String) {
        val deadline = System.currentTimeMillis() + BROWSER_SIGN_IN_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline) {
            delay(PAIRING_RETRY_MS)
            // The installer backed out to the form; stop waiting with them.
            if (launchState !is LaunchState.SignIn) return
            val registration = try {
                register(deviceId)
            } catch (_: Exception) {
                // A dropped connection is not a refusal -- the browser half may still be
                // mid-flow on a flaky panel. Keep waiting until the deadline.
                continue
            }
            if (registration.status != LaunchStateResolver.WAITING_PAIRING) {
                completePairing(registration.screenName, pairCode = null)
                return
            }
        }
        launchState = LaunchState.SignIn(busy = false)
    }

    /** Ask the server whether the phone half has finished, until it has or time runs out. */
    private suspend fun pollGoogleSignIn(started: GoogleStartResponse) {
        // Googles own floor, echoed back by the server. Polling faster earns `slow_down`,
        // not a token, so the interval is widened rather than ignored when that arrives.
        var intervalMs = started.interval.coerceAtLeast(5) * 1_000L
        val deadline = System.currentTimeMillis() + started.expires_in * 1_000L

        while (System.currentTimeMillis() < deadline) {
            delay(intervalMs)
            // The installer backed out to the sign-in form; stop polling with them.
            val showing = launchState as? LaunchState.GoogleSignIn ?: return

            val response = try {
                ApiClient.service(this).googlePoll(GooglePollRequest(started.poll_token))
            } catch (_: Exception) {
                // A dropped connection mid-approval is not a refusal. Keep waiting: the
                // code on screen is still valid and the installer still holds a phone.
                continue
            }

            if (!response.isSuccessful) {
                launchState = showing.copy(
                    error = when (response.code()) {
                        403 -> "That Google account cannot add screens here. " +
                            "Check it is on your MR Signage profile, with an owner or editor role."
                        401 -> "This sign-in expired. Go back and start again."
                        409 -> "This workspace has reached its screen limit."
                        else -> "Sign in failed (error ${response.code()})."
                    }
                )
                return
            }

            when (val status = response.body()?.status) {
                "bound" -> {
                    completePairing(
                        response.body()?.screen?.name,
                        pairCode = null,
                        deviceSecret = response.body()?.screen?.device_secret,
                    )
                    return
                }
                "pending" -> Unit
                "slow_down" -> intervalMs += 5_000L
                "denied" -> {
                    launchState = showing.copy(error = "That request was declined on the phone.")
                    return
                }
                "expired" -> {
                    launchState = showing.copy(error = "This code expired. Go back and start again.")
                    return
                }
                else -> {
                    launchState = showing.copy(error = "Unexpected reply from the server ($status).")
                    return
                }
            }
        }

        (launchState as? LaunchState.GoogleSignIn)?.let {
            launchState = it.copy(error = "This code expired. Go back and start again.")
        }
    }

    private fun startPairingFlow(deviceId: String) {
        pairingJob?.cancel()
        pairingJob = lifecycleScope.launch {
            usePairingCode(deviceId)
        }
    }

    private fun cancelPairingFlow() {
        pairingJob?.cancel()
        pairingJob = null
        launchState = LaunchState.SignIn()
    }

    /** Fallback route: mint a code here and let a dashboard user claim it, as before. */
    private suspend fun usePairingCode(deviceId: String) {
        launchState = LaunchState.Pairing()
        val resolved = LaunchStateResolver.refresh(LaunchState.Pairing()) { register(deviceId) }
        if (resolved is LaunchState.Playing) {
            completePairing(resolved.screenName, pairCode = null)
            return
        }

        launchState = resolved
        if (resolved is LaunchState.Pairing) {
            waitForPairing(deviceId, resolved)
        }
    }

    private suspend fun waitForPairing(deviceId: String, initialState: LaunchState.Pairing) {
        var pairCode = initialState.pairCode
        var codeIssuedAt = if (pairCode == null) 0L else System.currentTimeMillis()

        while (coroutineContext.isActive) {
            if (launchState !is LaunchState.Pairing) return

            try {
                if (pairCode == null || System.currentTimeMillis() - codeIssuedAt >= PAIR_CODE_REFRESH_MS) {
                    val registration = register(deviceId)
                    if (registration.status != LaunchStateResolver.WAITING_PAIRING) {
                        completePairing(registration.screenName, pairCode)
                        return
                    }

                    pairCode = registration.pairCode
                    codeIssuedAt = System.currentTimeMillis()
                    launchState = LaunchState.Pairing(pairCode = pairCode, connectionMessage = null, issuedAtMs = codeIssuedAt, ttlSeconds = PAIR_CODE_TTL_SECONDS)
                } else {
                    val response = ApiClient.service(this).sync(deviceId)
                    val body = response.body()
                    if (response.isSuccessful &&
                        body?.status != null &&
                        body.status != LaunchStateResolver.WAITING_PAIRING
                    ) {
                        completePairing(screenName = null, pairCode = pairCode)
                        return
                    }

                    if (!response.isSuccessful) {
                        throw IOException("Pairing check failed with HTTP ${response.code()}")
                    }

                    if (launchState is LaunchState.Pairing && (launchState as LaunchState.Pairing).connectionMessage != null) {
                        launchState = LaunchState.Pairing(pairCode = pairCode, connectionMessage = null, issuedAtMs = codeIssuedAt, ttlSeconds = PAIR_CODE_TTL_SECONDS)
                    }
                }
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                android.util.Log.w("MainActivity", "Pairing polling issue: ${e.message}")
                launchState = LaunchState.Pairing(
                    pairCode = pairCode,
                    connectionMessage = "No connection. Pairing will resume automatically.",
                    issuedAtMs = codeIssuedAt,
                    ttlSeconds = PAIR_CODE_TTL_SECONDS
                )
            }

            delay(PAIRING_RETRY_MS)
        }
    }

    private suspend fun register(deviceId: String): RegistrationSnapshot {
        val response = ApiClient.service(this).register(
            RegisterRequest(
                device_id = deviceId,
                installation_id = deviceState.installationId,
                hardware_name = deviceState.hardwareName,
                device_model = deviceState.deviceModel,
                manufacturer = deviceState.manufacturer,
                identity_source = deviceState.identitySource
            )
        )
        if (!response.isSuccessful) {
            throw IOException("Registration failed with HTTP ${response.code()}")
        }

        val screen = response.body() ?: throw IOException("Registration returned no screen")
        // Delivered exactly once, on the first poll after an operator redeemed the pairing
        // code. The /pair response goes to their dashboard, so this is the only channel a
        // pair-code screen has for learning its own credential.
        screen.device_secret?.takeIf { it.isNotBlank() }?.let {
            deviceState.setDeviceSecret(it)
            ApiClient.clearToken()
        }
        return RegistrationSnapshot(
            status = screen.status,
            pairCode = screen.pair_code,
            screenName = screen.name
        )
    }

    private fun completePairing(screenName: String?, pairCode: String?, deviceSecret: String? = null) {
        pairingJob?.cancel()
        pairingJob = null
        val fallbackName = pairCode?.let { "Screen $it" } ?: DeviceState.DEFAULT_SCREEN_NAME
        // The server returns this exactly once, from whichever route bound the screen. It
        // is the credential every later request authenticates with, so it is stored before
        // anything else and any token cached against a previous pairing is dropped.
        if (!deviceSecret.isNullOrBlank()) {
            deviceState.setDeviceSecret(deviceSecret)
            ApiClient.clearToken()
        }
        deviceState.markPaired(screenName ?: fallbackName)
        launchState = LaunchState.Playing(deviceState.screenName)
        PlaybackService.start(this, launchPlayer = false)
        PlaybackService.requestImmediateSync(this)
    }

    private fun saveServerUrl(value: String) {
        try {
            val normalized = ApiClient.normalizeBaseUrl(value)
            deviceState.setApiBaseUrlOverride(normalized)
            serverError = null
            serverRevision += 1
            if (!deviceState.isPaired) launchState = LaunchState.CheckingLocalState
        } catch (exception: IllegalArgumentException) {
            serverError = exception.message ?: "Invalid server URL"
        }
    }

    /**
     * The system's "use MR Signage as your home app?" dialog.
     *
     * It must be opened for a result. Opened with startActivity it has no calling package, and
     * the permission controller closes it without showing anything -- which is why "Choose MR Signage
     * as TV launcher" never did anything on the KONKA.
     */
    private val homeRoleRequest = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        com.olrac.signage.boot.PlayerLauncher.releaseHold()
        defaultHome = isDefaultHomeLauncher()
        getSharedPreferences("signage_prefs", Context.MODE_PRIVATE).edit()
            .putString(PREF_HOME_ROLE_RESULT, "${System.currentTimeMillis()} result=${result.resultCode} held=$defaultHome")
            .apply()
        android.util.Log.i("MainActivity", "Home role request finished: held=$defaultHome")
    }

    private fun requestHomeRole() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val roleManager = getSystemService(RoleManager::class.java)
            if (roleManager.isRoleAvailable(RoleManager.ROLE_HOME)) {
                com.olrac.signage.boot.PlayerLauncher.holdWhileSystemDialogOpen(this)
                runCatching { homeRoleRequest.launch(roleManager.createRequestRoleIntent(RoleManager.ROLE_HOME)) }
                    .onSuccess { return }
                com.olrac.signage.boot.PlayerLauncher.releaseHold()
            }
        }
        runCatching { startActivity(Intent(Settings.ACTION_HOME_SETTINGS)) }
    }

    /**
     * On a TV that cannot bring the player back after a restart, ask once to become its home app.
     *
     * Android 10+ lets an app open itself after boot only as device owner, with "Display over
     * other apps", or as the home app. The KONKA is a low-RAM TV, where the overlay switch never
     * takes effect, so the home app is the one way left -- and the system opens the home app
     * first after every restart. One "Yes" on the system's own dialog; asked a single time, after
     * that only from the maintenance screen.
     */
    private fun askOnceToBecomeHome() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q || defaultHome) return
        // Ask on TVs too. On the emulator the built-in launcher kept winning even after a grant,
        // but that is per-firmware and the KONKA is the real test -- and this is the only route an
        // operator can take with just the remote, no ADB. isEffectiveHome reports the real result.
        if (com.olrac.signage.boot.PlayerLauncher.canStartFromBackground(this)) return
        if (showPinPrompt || showServerSetup || operatorExited()) return
        val prefs = getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
        if (prefs.contains(PREF_HOME_ROLE_ASKED_AT)) return
        if (!getSystemService(RoleManager::class.java).isRoleAvailable(RoleManager.ROLE_HOME)) return
        prefs.edit().putLong(PREF_HOME_ROLE_ASKED_AT, System.currentTimeMillis()).apply()
        requestHomeRole()
    }

    private fun isDefaultHomeLauncher(): Boolean = com.olrac.signage.boot.PlayerLauncher.isEffectiveHome(this)

    private fun configurePlayerWindow() {
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
            WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD or
            WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_HIDDEN)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        hideSystemBars()
    }

    private fun hideSystemBars() {
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    companion object {
        const val PREF_PLAYER_RESUMED_AT = "player_last_resumed_at"
        const val PREF_OPERATOR_EXIT_AT = "operator_exit_at"
        // How long the watchdog stands down after a PIN opens maintenance / after an exit, so it
        // stops reclaiming the screen from Settings and the home launcher while the operator works.
        const val PREF_WATCHDOG_SUPPRESS_UNTIL = "watchdog_suppress_until"
        /** While set, the operator is in Settings flipping a setup switch and wants bringing back. */
        /** Declared by PlayerLauncher, which is the choke point that has to honour it. */
        const val PREF_SETUP_AWAITING_UNTIL = com.olrac.signage.boot.PlayerLauncher.PREF_SETUP_AWAITING_UNTIL
        private const val SETUP_RETURN_WINDOW_MS = 5 * 60_000L
        private const val MAINTENANCE_SUPPRESS_MS = 15 * 60_000L
        private const val EXIT_SUPPRESS_MS = 30 * 60_000L
        // v3: 1.0.28 gated the ask off for TVs; re-open it for the KONKA test.
        private const val PREF_HOME_ROLE_ASKED_AT = "home_role_asked_v3_at"
        const val PREF_HOME_ROLE_RESULT = "home_role_request_result"
        /** Set by the command poll in PlaybackService on a "remote_unlock" command; consumed
         *  by the polling LaunchedEffect below, which opens maintenance as if the PIN had
         *  just been entered correctly. */
        const val PREF_PENDING_REMOTE_UNLOCK = "pending_remote_unlock"

        /** Whether the player is on screen. Read by PlaybackService, which shares the process. */
        @Volatile var visible = false
            private set

        @Volatile var activeActivity: MainActivity? = null

        fun onKioskLockChanged(context: Context, locked: Boolean) {
            val prefs = context.getSharedPreferences("signage_prefs", Context.MODE_PRIVATE)
            prefs.edit().putBoolean("kiosk_lock", locked).apply()
            if (locked) {
                prefs.edit().remove(PREF_OPERATOR_EXIT_AT).remove(PREF_WATCHDOG_SUPPRESS_UNTIL).apply()
            } else {
                prefs.edit().putLong(PREF_OPERATOR_EXIT_AT, System.currentTimeMillis())
                    .putLong(PREF_WATCHDOG_SUPPRESS_UNTIL, Long.MAX_VALUE).apply()
            }
            activeActivity?.setKioskLockState(locked, reportToServer = false)
            if (locked && !visible) {
                com.olrac.signage.boot.PlayerLauncher.launch(context, delayMs = 0L, reason = "remote_lock")
            } else if (!locked && activeActivity == null) {
                DeviceOwnerManager.releaseKioskPolicy(context)
            }
        }
        private const val PAIRING_RETRY_MS = 5_000L

        // Boot reconnect backoff. Starts quick because the common case is a TV that booted a
        // few seconds before its Wi-Fi associated, and widens to a minute so a panel left
        // running through a long outage is not hammering a server that is already down. It
        // never gives up: the screen is claimed, and the only thing missing is the network.
        private const val RECONNECT_FIRST_RETRY_MS = 3_000L
        private const val RECONNECT_MAX_RETRY_MS = 60_000L

        // How long to keep watching for the browser half to bind this screen. Generous
        // because it is a person signing into Google on a TV remote, which is slow.
        private const val BROWSER_SIGN_IN_TIMEOUT_MS = 10 * 60_000L
        // Strictly INSIDE the server's PAIR_CODE_TTL (one minute), never equal to it.
        // Both were sixty seconds, so the code expired at the same instant this replaced it
        // -- and since the loop below only ticks every few seconds, the digits on screen were
        // an already-expired code for up to five seconds of every minute. Typing them then
        // earned "Invalid pairing code".
        //
        // Refreshing early costs nothing: /register hands back the SAME code while it is
        // still valid rather than minting a new one, so this only has to beat expiry.
        private const val PAIR_CODE_REFRESH_MS = 4 * 60_000L

        /** What the server actually grants, so the countdown on the TV is not a fiction. */
        private const val PAIR_CODE_TTL_SECONDS = 300
    }
}

