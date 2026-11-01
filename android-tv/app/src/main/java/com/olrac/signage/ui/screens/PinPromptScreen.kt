package com.olrac.signage.ui.screens

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.olrac.signage.data.PinAttemptLimiter
import com.olrac.signage.telemetry.KioskAttemptReporter
import kotlinx.coroutines.delay

@Composable
fun PinPromptScreen(
    expectedPin: String,
    /**
     * The platform operator's master code, accepted alongside [expectedPin] rather than
     * instead of it. A support engineer standing at a panel they have never seen cannot be
     * reading a different four digits off a dashboard for every screen -- least of all when
     * the reason they are there is that the screen has no network and the dashboard shows
     * nothing useful. Null when the operator has not set one.
     */
    masterPin: String? = null,
    onUnlocked: () -> Unit,
    onCancel: () -> Unit
) {
    BackHandler(onBack = onCancel)
    val context = LocalContext.current

    var entered by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    // Loaded once per reveal, not reset by it -- the whole point is that repeating the
    // gesture does not hand out five fresh guesses.
    var limiterState by remember { mutableStateOf(PinAttemptLimiter.load(context)) }
    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val cooldownRemaining = PinAttemptLimiter.cooldownRemainingMs(limiterState, nowMs)

    // Ticks once a second only while a cooldown is actually counting down, so the
    // "Xs left" message updates without the rest of the screen recomposing constantly.
    LaunchedEffect(limiterState.cooldownUntil) {
        while (PinAttemptLimiter.cooldownRemainingMs(limiterState, System.currentTimeMillis()) > 0) {
            delay(1_000L)
            nowMs = System.currentTimeMillis()
        }
    }

    fun submit() {
        val correct = entered == expectedPin || (masterPin != null && entered == masterPin)
        KioskAttemptReporter.report(context, correct)
        if (correct) {
            limiterState = PinAttemptLimiter.recordSuccess()
            PinAttemptLimiter.save(context, limiterState)
            onUnlocked()
            return
        }
        val now = System.currentTimeMillis()
        limiterState = PinAttemptLimiter.recordFailure(limiterState, now)
        PinAttemptLimiter.save(context, limiterState)
        entered = ""
        nowMs = now
        error = if (PinAttemptLimiter.cooldownRemainingMs(limiterState, now) > 0) {
            "Too many wrong attempts."
        } else {
            "Incorrect pin."
        }
    }

    SetupSurface {
        Text(text = "Maintenance access", color = Color.White)
        Text(
            text = "Enter this screen's 4-digit pin. It is shown on the screen's page in the dashboard.",
            color = Color.LightGray,
            textAlign = TextAlign.Center
        )
        Spacer(modifier = Modifier.height(24.dp))
        OutlinedTextField(
            value = entered,
            onValueChange = { typed ->
                entered = typed.filter { it.isDigit() }.take(4)
                error = null
            },
            enabled = cooldownRemaining <= 0,
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Pin") },
            supportingText = run {
                val message = if (cooldownRemaining > 0) {
                    "Too many wrong attempts. Try again in ${cooldownRemaining / 1000 + 1}s."
                } else {
                    error
                }
                message?.let { text -> { Text(text) } }
            },
            isError = error != null || cooldownRemaining > 0,
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            colors = setupFieldColors()
        )
        Spacer(modifier = Modifier.height(16.dp))
        Button(
            onClick = { submit() },
            enabled = entered.length == 4 && cooldownRemaining <= 0,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(containerColor = AccentGreen, contentColor = Color.Black)
        ) {
            Text("Unlock")
        }
        Spacer(modifier = Modifier.height(8.dp))
        TextButton(onClick = onCancel) {
            Text("Back to player", color = Color.LightGray)
        }
    }
}
