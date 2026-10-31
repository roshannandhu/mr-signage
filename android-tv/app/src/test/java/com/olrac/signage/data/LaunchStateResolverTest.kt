package com.olrac.signage.data

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class LaunchStateResolverTest {
    @Test
    fun pairedDevicePlaysWhenRegistrationThrows() = runBlocking {
        val localState = LaunchStateResolver.resolveLocal(
            isPaired = true,
            hasLocalPlaylist = false
        )
        val state = LaunchStateResolver.refresh(
            currentState = localState,
            register = { throw IOException("offline") }
        )

        assertTrue(state is LaunchState.Playing)
    }

    @Test
    fun cachedPlaylistIsIndependentProofOfPairing() {
        val state = LaunchStateResolver.resolveLocal(
            isPaired = false,
            hasLocalPlaylist = true
        )

        assertTrue(state is LaunchState.Playing)
    }

    @Test
    fun unclaimedDeviceAsksForSignInRatherThanMintingACode() {
        val state = LaunchStateResolver.resolveLocal(
            isPaired = false,
            hasLocalPlaylist = false
        )

        // Signing in on the TV is the default route now. Returning Pairing here would send
        // every boot back to registering for a code nobody is looking at.
        assertTrue("expected SignIn, got $state", state is LaunchState.SignIn)
    }

    @Test
    fun unpairedOfflineDeviceShowsRecoverableConnectionMessage() = runBlocking {
        // The pairing fallback is entered explicitly, so that is the state refresh sees.
        val state = LaunchStateResolver.refresh(
            currentState = LaunchState.Pairing(),
            register = { throw IOException("offline") }
        )

        assertTrue(state is LaunchState.Pairing)
        assertEquals(
            "No connection. Pairing will resume automatically.",
            (state as LaunchState.Pairing).connectionMessage
        )
    }

    @Test
    fun forcedSignInIsHonouredOnlyForAnUnpairedScreen() {
        // The player asks for it itself, after wiping its own pairing.
        assertTrue(LaunchStateResolver.honourForcedSignIn(requested = true, isPaired = false))
        // Another app asking a paired screen to forget everything must be ignored.
        assertFalse(LaunchStateResolver.honourForcedSignIn(requested = true, isPaired = true))
        assertFalse(LaunchStateResolver.honourForcedSignIn(requested = false, isPaired = false))
        assertFalse(LaunchStateResolver.honourForcedSignIn(requested = false, isPaired = true))
    }
}
