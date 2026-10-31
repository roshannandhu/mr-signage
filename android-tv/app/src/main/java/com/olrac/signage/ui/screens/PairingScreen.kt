package com.olrac.signage.ui.screens

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.olrac.signage.data.LaunchState
import kotlinx.coroutines.delay

@Composable
fun PairingScreen(
    state: LaunchState.Pairing,
    onBackToSignIn: () -> Unit
) {
    BackHandler(onBack = onBackToSignIn)

    val rawCode = state.pairCode?.filter { it.isDigit() }
    val formattedCode = when (rawCode?.length) {
        6 -> "${rawCode.substring(0, 3)} ${rawCode.substring(3)}"
        else -> null
    }

    SetupSurface {
        Text(
            text = "MR SIGNAGE",
            color = AccentGreen,
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.Bold,
            letterSpacing = 2.sp
        )

        Spacer(modifier = Modifier.height(8.dp))

        Text(
            text = "Pair Your Display",
            style = MaterialTheme.typography.headlineLarge,
            fontWeight = FontWeight.Bold,
            color = Color.White
        )

        Spacer(modifier = Modifier.height(10.dp))

        Text(
            text = "Enter this 6-digit code in your MR Signage admin dashboard to link this screen.",
            color = Color(0xFF94A3B8),
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center
        )

        Spacer(modifier = Modifier.height(32.dp))

        // Pairing Code Card
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(16.dp),
            color = Color(0xFF131924),
            border = BorderStroke(1.dp, Color(0xFF263345))
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 32.dp, horizontal = 24.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                if (formattedCode != null) {
                    Text(
                        text = "PAIRING CODE",
                        color = Color(0xFF8A99AD),
                        style = MaterialTheme.typography.labelMedium,
                        letterSpacing = 2.sp
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = formattedCode,
                        color = Color.White,
                        fontSize = 44.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        letterSpacing = 6.sp,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center
                    ) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(14.dp),
                            color = if (state.connectionMessage != null) Color(0xFFFFB74D) else AccentGreen,
                            strokeWidth = 2.dp
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = state.connectionMessage ?: "Waiting for dashboard confirmation...",
                            color = if (state.connectionMessage != null) Color(0xFFFFB74D) else AccentGreen,
                            style = MaterialTheme.typography.bodySmall
                        )
                    }

                    Spacer(modifier = Modifier.height(16.dp))
                    var remainingSeconds by remember(state.pairCode, state.issuedAtMs) {
                        val elapsed = (System.currentTimeMillis() - state.issuedAtMs) / 1000L
                        mutableIntStateOf((state.ttlSeconds - elapsed.toInt()).coerceIn(0, state.ttlSeconds))
                    }

                    LaunchedEffect(state.pairCode, state.issuedAtMs) {
                        while (remainingSeconds > 0) {
                            delay(1000L)
                            remainingSeconds--
                        }
                    }

                    Column(
                        modifier = Modifier.fillMaxWidth(0.6f),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        LinearProgressIndicator(
                            progress = remainingSeconds / state.ttlSeconds.toFloat(),
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(4.dp)
                                .clip(RoundedCornerShape(2.dp)),
                            color = AccentGreen,
                            trackColor = Color(0xFF1E293B)
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = "Code rotates in ${remainingSeconds}s",
                            color = Color(0xFF64748B),
                            style = MaterialTheme.typography.labelSmall
                        )
                    }
                } else {
                    CircularProgressIndicator(
                        modifier = Modifier.size(36.dp),
                        color = AccentGreen,
                        strokeWidth = 3.dp
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    Text(
                        text = state.connectionMessage ?: "Requesting pairing code...",
                        color = Color.LightGray,
                        style = MaterialTheme.typography.bodyMedium,
                        textAlign = TextAlign.Center
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(28.dp))

        Button(
            onClick = onBackToSignIn,
            modifier = Modifier
                .fillMaxWidth()
                .height(50.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = Color(0xFF1E293B),
                contentColor = Color.White
            ),
            border = BorderStroke(1.dp, Color(0xFF334155)),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text("Back to Sign In", color = Color.White)
        }
    }
}
