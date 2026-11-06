package com.olrac.signage.ui.screens

import android.content.Context
import android.view.KeyEvent
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Backspace
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.ripple.rememberRipple
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.olrac.signage.data.PinAttemptLimiter
import com.olrac.signage.telemetry.KioskAttemptReporter
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/**
 * Ultra-Modern ATM / iPhone-Style Clean PIN Pad.
 *
 * Professional on-screen keypad:
 * - 4 Glowing indicator dots (● ○ ○ ○)
 * - Tactile 3x4 circular keypad with ripple and TV remote D-pad support
 * - Automatic submission on 4th digit
 * - Error shake animation on wrong PIN
 * - Full attempt limiter and cooldown safety
 */
@Composable
fun PinPromptScreen(
    expectedPin: String,
    masterPin: String? = null,
    onUnlocked: () -> Unit,
    onCancel: () -> Unit
) {
    BackHandler(onBack = onCancel)
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()

    var entered by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    val shakeOffset = remember { Animatable(0f) }

    var limiterState by remember { mutableStateOf(PinAttemptLimiter.load(context)) }
    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val cooldownRemaining = PinAttemptLimiter.cooldownRemainingMs(limiterState, nowMs)

    // Cooldown ticker
    LaunchedEffect(limiterState.cooldownUntil) {
        while (PinAttemptLimiter.cooldownRemainingMs(limiterState, System.currentTimeMillis()) > 0) {
            delay(1000L)
            nowMs = System.currentTimeMillis()
        }
    }

    fun triggerShake() {
        coroutineScope.launch {
            shakeOffset.snapTo(0f)
            shakeOffset.animateTo(
                targetValue = 0f,
                animationSpec = tween(durationMillis = 350)
            )
        }
    }

    fun submit(pinToCheck: String = entered) {
        if (cooldownRemaining > 0) return
        val correct = pinToCheck == expectedPin || (masterPin != null && pinToCheck == masterPin)
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
            "Incorrect PIN"
        }
        triggerShake()
    }

    fun appendDigit(digit: String) {
        if (cooldownRemaining > 0 || entered.length >= 4) return
        error = null
        val newPin = entered + digit
        entered = newPin
        if (newPin.length == 4) {
            submit(newPin)
        }
    }

    fun backspace() {
        if (entered.isNotEmpty()) {
            entered = entered.dropLast(1)
            error = null
        }
    }

    // Capture physical remote 0-9 number keys
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF07090E))
            .onPreviewKeyEvent { keyEvent ->
                if (keyEvent.nativeKeyEvent.action == KeyEvent.ACTION_DOWN) {
                    when (keyEvent.nativeKeyEvent.keyCode) {
                        KeyEvent.KEYCODE_0 -> { appendDigit("0"); true }
                        KeyEvent.KEYCODE_1 -> { appendDigit("1"); true }
                        KeyEvent.KEYCODE_2 -> { appendDigit("2"); true }
                        KeyEvent.KEYCODE_3 -> { appendDigit("3"); true }
                        KeyEvent.KEYCODE_4 -> { appendDigit("4"); true }
                        KeyEvent.KEYCODE_5 -> { appendDigit("5"); true }
                        KeyEvent.KEYCODE_6 -> { appendDigit("6"); true }
                        KeyEvent.KEYCODE_7 -> { appendDigit("7"); true }
                        KeyEvent.KEYCODE_8 -> { appendDigit("8"); true }
                        KeyEvent.KEYCODE_9 -> { appendDigit("9"); true }
                        KeyEvent.KEYCODE_DEL -> { backspace(); true }
                        KeyEvent.KEYCODE_BACK -> { onCancel(); true }
                        else -> false
                    }
                } else false
            }
    ) {
        // Close Button in top right
        Surface(
            shape = CircleShape,
            color = Color(0xFF1E283D),
            border = BorderStroke(1.dp, Color(0xFF334155)),
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(24.dp)
                .size(40.dp)
                .clickable(onClick = onCancel)
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(
                    imageVector = Icons.Rounded.Close,
                    contentDescription = "Cancel",
                    tint = Color.White,
                    modifier = Modifier.size(18.dp)
                )
            }
        }

        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier
                .offset { IntOffset(shakeOffset.value.roundToInt(), 0) }
                .padding(24.dp)
        ) {
            // Lock Icon Badge
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier
                    .size(52.dp)
                    .background(Color(0x2610B981), CircleShape)
            ) {
                Icon(
                    imageVector = Icons.Rounded.Lock,
                    contentDescription = null,
                    tint = Color(0xFF10B981),
                    modifier = Modifier.size(26.dp)
                )
            }

            Spacer(modifier = Modifier.height(14.dp))

            Text(
                text = "Security Clearance",
                color = Color.White,
                fontSize = 19.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.SansSerif,
                textAlign = TextAlign.Center
            )

            Spacer(modifier = Modifier.height(4.dp))

            Text(
                text = if (cooldownRemaining > 0) {
                    "Too many wrong attempts. Try again in ${cooldownRemaining / 1000 + 1}s."
                } else if (error != null) {
                    error!!
                } else {
                    "Enter 4-digit maintenance PIN"
                },
                color = if (error != null || cooldownRemaining > 0) Color(0xFFF87171) else Color(0xFF94A3B8),
                fontSize = 12.5.sp,
                fontFamily = FontFamily.SansSerif,
                textAlign = TextAlign.Center
            )

            Spacer(modifier = Modifier.height(26.dp))

            // 4 Indicator Dots
            Row(
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                repeat(4) { index ->
                    val isFilled = index < entered.length
                    val dotColor = when {
                        error != null || cooldownRemaining > 0 -> Color(0xFFEF4444)
                        isFilled -> Color(0xFF10B981)
                        else -> Color.Transparent
                    }
                    val borderColor = when {
                        error != null || cooldownRemaining > 0 -> Color(0xFFEF4444)
                        isFilled -> Color(0xFF10B981)
                        else -> Color(0xFF334155)
                    }

                    Box(
                        modifier = Modifier
                            .size(16.dp)
                            .background(dotColor, CircleShape)
                            .then(
                                if (!isFilled) Modifier.background(Color(0xFF1E283D), CircleShape) else Modifier
                            )
                    ) {
                        Surface(
                            shape = CircleShape,
                            color = dotColor,
                            border = BorderStroke(1.5.dp, borderColor),
                            modifier = Modifier.fillMaxSize()
                        ) {}
                    }
                }
            }

            Spacer(modifier = Modifier.height(30.dp))

            // 3x4 Circular Numeric Keypad
            val keypad = listOf(
                listOf("1", "2", "3"),
                listOf("4", "5", "6"),
                listOf("7", "8", "9"),
                listOf("clear", "0", "backspace")
            )

            Column(
                verticalArrangement = Arrangement.spacedBy(12.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                for (row in keypad) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(18.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        for (key in row) {
                            when (key) {
                                "clear" -> {
                                    KeypadActionButton(
                                        text = "Clear",
                                        onClick = {
                                            entered = ""
                                            error = null
                                        },
                                        enabled = entered.isNotEmpty()
                                    )
                                }
                                "backspace" -> {
                                    KeypadIconButton(
                                        icon = Icons.Rounded.Backspace,
                                        onClick = ::backspace,
                                        enabled = entered.isNotEmpty()
                                    )
                                }
                                else -> {
                                    KeypadNumberButton(
                                        number = key,
                                        onClick = { appendDigit(key) },
                                        enabled = cooldownRemaining <= 0 && entered.length < 4
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun KeypadNumberButton(
    number: String,
    onClick: () -> Unit,
    enabled: Boolean
) {
    var isFocused by remember { mutableStateOf(false) }

    Surface(
        shape = CircleShape,
        color = if (isFocused) Color(0xFF2E3E5B) else Color(0xFF131A29),
        border = BorderStroke(
            1.2.dp,
            if (isFocused) Color(0xFF10B981) else Color(0xFF1E293B)
        ),
        shadowElevation = if (isFocused) 6.dp else 2.dp,
        modifier = Modifier
            .size(64.dp)
            .scale(if (isFocused) 1.08f else 1f)
            .onFocusChanged { isFocused = it.isFocused }
            .focusable(enabled)
            .clickable(
                enabled = enabled,
                interactionSource = remember { MutableInteractionSource() },
                indication = rememberRipple(bounded = true, color = Color(0xFF10B981)),
                onClick = onClick
            )
    ) {
        Box(contentAlignment = Alignment.Center) {
            Text(
                text = number,
                color = if (enabled) Color.White else Color(0xFF64748B),
                fontSize = 22.sp,
                fontWeight = FontWeight.SemiBold,
                fontFamily = FontFamily.SansSerif
            )
        }
    }
}

@Composable
private fun KeypadIconButton(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    onClick: () -> Unit,
    enabled: Boolean
) {
    var isFocused by remember { mutableStateOf(false) }

    Surface(
        shape = CircleShape,
        color = if (isFocused) Color(0xFF2E3E5B) else Color(0xFF131A29),
        border = BorderStroke(
            1.2.dp,
            if (isFocused) Color(0xFF10B981) else Color(0xFF1E293B)
        ),
        modifier = Modifier
            .size(64.dp)
            .scale(if (isFocused) 1.08f else 1f)
            .onFocusChanged { isFocused = it.isFocused }
            .focusable(enabled)
            .clickable(
                enabled = enabled,
                interactionSource = remember { MutableInteractionSource() },
                indication = rememberRipple(bounded = true, color = Color(0xFF10B981)),
                onClick = onClick
            )
    ) {
        Box(contentAlignment = Alignment.Center) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = if (enabled) Color.White else Color(0xFF475569),
                modifier = Modifier.size(22.dp)
            )
        }
    }
}

@Composable
private fun KeypadActionButton(
    text: String,
    onClick: () -> Unit,
    enabled: Boolean
) {
    var isFocused by remember { mutableStateOf(false) }

    Surface(
        shape = CircleShape,
        color = if (isFocused) Color(0xFF2E3E5B) else Color(0xFF131A29),
        border = BorderStroke(
            1.2.dp,
            if (isFocused) Color(0xFF10B981) else Color(0xFF1E293B)
        ),
        modifier = Modifier
            .size(64.dp)
            .scale(if (isFocused) 1.08f else 1f)
            .onFocusChanged { isFocused = it.isFocused }
            .focusable(enabled)
            .clickable(
                enabled = enabled,
                interactionSource = remember { MutableInteractionSource() },
                indication = rememberRipple(bounded = true, color = Color(0xFF10B981)),
                onClick = onClick
            )
    ) {
        Box(contentAlignment = Alignment.Center) {
            Text(
                text = text,
                color = if (enabled) Color.White else Color(0xFF475569),
                fontSize = 13.sp,
                fontWeight = FontWeight.Medium,
                fontFamily = FontFamily.SansSerif
            )
        }
    }
}
