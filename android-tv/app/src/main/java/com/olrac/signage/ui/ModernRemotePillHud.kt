package com.olrac.signage.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
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
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.LockOpen
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.ripple.rememberRipple
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay

/**
 * State representation for the high-end 3.5s floating status pill.
 */
data class RemotePillState(
    val title: String,
    val subtitle: String,
    val isLocked: Boolean,
    val timestamp: Long = System.currentTimeMillis()
)

/**
 * Ultra-Modern 3-Second Floating Dynamic Pill.
 *
 * Inspired by Apple VisionOS / Dynamic Island glassmorphism:
 * - Glides smoothly down from the top center
 * - Compact width (<400dp), never covers screen width
 * - Pure obsidian glass (#0A0F1D, 95% opacity) with subtle glowing border
 * - 2-line crisp micro-typography + glowing animated status icon
 * - Auto-dismisses smoothly after 3.5 seconds
 * - Never blocks or resizes active ad playback underneath
 */
@Composable
fun ModernRemotePillHud(
    pillState: RemotePillState?,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier
) {
    LaunchedEffect(pillState?.timestamp) {
        if (pillState != null) {
            delay(3_500L)
            onDismiss()
        }
    }

    val visible = pillState != null

    AnimatedVisibility(
        visible = visible,
        enter = slideInVertically(
            initialOffsetY = { -it * 2 },
            animationSpec = spring(dampingRatio = 0.75f, stiffness = 400f)
        ) + fadeIn(animationSpec = tween(220)) + scaleIn(initialScale = 0.88f),
        exit = slideOutVertically(
            targetOffsetY = { -it * 2 },
            animationSpec = tween(300, easing = FastOutSlowInEasing)
        ) + fadeOut(animationSpec = tween(220)) + scaleOut(targetScale = 0.94f),
        modifier = modifier
    ) {
        if (pillState != null) {
            val isLocked = pillState.isLocked
            val accentColor = if (isLocked) Color(0xFF6366F1) else Color(0xFF10B981)
            val glowColor = if (isLocked) Color(0xFF818CF8) else Color(0xFF34D399)
            val iconBg = if (isLocked) Color(0x336366F1) else Color(0x3310B981)

            Surface(
                shape = RoundedCornerShape(32.dp),
                color = Color(0xF2080D1A),
                border = BorderStroke(1.dp, accentColor.copy(alpha = 0.5f)),
                shadowElevation = 16.dp,
                modifier = Modifier
                    .widthIn(max = 420.dp)
                    .padding(horizontal = 16.dp)
                    .clickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = rememberRipple(bounded = true, color = accentColor),
                        onClick = onDismiss
                    )
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 9.dp)
                ) {
                    // Glowing Icon Pill
                    Box(
                        contentAlignment = Alignment.Center,
                        modifier = Modifier
                            .size(34.dp)
                            .background(iconBg, CircleShape)
                    ) {
                        Icon(
                            imageVector = if (isLocked) Icons.Rounded.Lock else Icons.Rounded.LockOpen,
                            contentDescription = null,
                            tint = glowColor,
                            modifier = Modifier.size(18.dp)
                        )
                    }

                    Spacer(modifier = Modifier.width(12.dp))

                    // Text Details
                    Column(
                        verticalArrangement = Arrangement.Center
                    ) {
                        Text(
                            text = pillState.title,
                            color = Color.White,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.SansSerif,
                            letterSpacing = 0.2.sp
                        )
                        Text(
                            text = pillState.subtitle,
                            color = Color(0xFF94A3B8),
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Normal,
                            fontFamily = FontFamily.SansSerif
                        )
                    }
                }
            }
        }
    }
}

/**
 * Floating Quick Settings Gear Icon.
 *
 * Appears ONLY when the screen is Remotely Unlocked:
 * - Provides immediate on-site technician access to the Setup Hub with ZERO PIN required
 * - Completely hidden when locked so customers never see it.
 */
@Composable
fun FloatingQuickSettingsGear(
    visible: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val infiniteTransition = rememberInfiniteTransition(label = "gearPulse")
    val pulseScale by infiniteTransition.animateFloat(
        initialValue = 1.0f,
        targetValue = 1.08f,
        animationSpec = infiniteRepeatable(
            animation = tween(1200, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "gearPulseScale"
    )

    AnimatedVisibility(
        visible = visible,
        enter = fadeIn(tween(300)) + scaleIn(initialScale = 0.7f),
        exit = fadeOut(tween(250)) + scaleOut(targetScale = 0.7f),
        modifier = modifier
    ) {
        Surface(
            shape = RoundedCornerShape(22.dp),
            color = Color(0xEB0A0F1D),
            border = BorderStroke(1.dp, Color(0xFF10B981).copy(alpha = 0.6f)),
            shadowElevation = 12.dp,
            modifier = Modifier
                .size(44.dp)
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = rememberRipple(bounded = true, color = Color(0xFF10B981)),
                    onClick = onClick
                )
        ) {
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier.scale(pulseScale)
            ) {
                Icon(
                    imageVector = Icons.Rounded.Settings,
                    contentDescription = "Quick Maintenance",
                    tint = Color(0xFF34D399),
                    modifier = Modifier.size(22.dp)
                )
            }
        }
    }
}
