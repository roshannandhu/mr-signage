package com.olrac.signage.data

import android.view.KeyEvent
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The rules the Activity applies to every key before the view tree sees it. Running first is
 * what makes the gesture reachable from a screen that has buttons on it, and also what would
 * break every button on that screen if this took one key more than it needs.
 */
class MaintenanceKeyDispatcherTest {

    private val up = KeyEvent.KEYCODE_DPAD_UP
    private val down = KeyEvent.KEYCODE_DPAD_DOWN
    private val ok = KeyEvent.KEYCODE_DPAD_CENTER

    private fun press(
        d: MaintenanceKeyDispatcher,
        keyCode: Int,
        nowMs: Long,
        action: Int = KeyEvent.ACTION_DOWN,
        repeatCount: Int = 0,
        gestureEnabled: Boolean = true,
        surface: KeySurface = KeySurface.PLAYER,
        kioskLocked: Boolean = true
    ) = d.onKeyEvent(action, keyCode, repeatCount, gestureEnabled, nowMs, surface, kioskLocked)

    private val back = KeyEvent.KEYCODE_BACK

    @Test
    fun `back over the player is swallowed rather than leaving the ads`() {
        // Regression: BACK was handled nowhere, so the default finished the Activity and the
        // viewer was left on the TV's launcher -- no maintenance gesture required.
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.CONSUME, press(d, back, 0))
        assertEquals(KeyOutcome.CONSUME, press(d, back, 1, action = KeyEvent.ACTION_UP))
    }

    @Test
    fun `back over the player passes through once kiosk lock is off`() {
        // Phase 3.10: a plan without the TV lock feature (or an owner who turned it off)
        // leaves BACK as ordinary navigation -- it is not guarding anything anymore.
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, back, 0, kioskLocked = false))
        assertEquals(
            KeyOutcome.PASS_THROUGH,
            press(d, back, 1, action = KeyEvent.ACTION_UP, kioskLocked = false)
        )
    }

    @Test
    fun `an unspecified kiosk lock defaults to locked`() {
        // The caller omitting the parameter (an older call site, or a test that predates
        // this) must get today's behaviour, not an accidental unlock.
        val d = MaintenanceKeyDispatcher()
        assertEquals(
            KeyOutcome.CONSUME,
            d.onKeyEvent(KeyEvent.ACTION_DOWN, back, 0, gestureEnabled = true, nowMs = 0)
        )
    }

    @Test
    fun `kiosk lock off does not affect the pin prompt or setup screen's own back handling`() {
        // The flag only changes what BACK means over the bare player; a prompt that owns
        // its own BACK (or that is mid-entry) is unaffected either way.
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, back, 0, surface = KeySurface.SERVER_SETUP, kioskLocked = false))
        assertEquals(KeyOutcome.CONSUME, press(d, back, 1, surface = KeySurface.PIN_PROMPT, kioskLocked = false))
        assertEquals(
            KeyOutcome.CANCEL_PIN,
            press(d, back, 2, action = KeyEvent.ACTION_UP, surface = KeySurface.PIN_PROMPT, kioskLocked = false)
        )
    }

    @Test
    fun `the maintenance gesture still works with kiosk lock off`() {
        // Unlocking BACK does not remove the maintenance screen itself -- it may still be
        // wanted for settings or diagnostics even when nothing is trapping the viewer.
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, up, 0, kioskLocked = false))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, up, 1, kioskLocked = false))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, down, 2, kioskLocked = false))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, down, 3, kioskLocked = false))
        assertEquals(KeyOutcome.REVEAL_PIN, press(d, ok, 4, kioskLocked = false))
    }

    @Test
    fun `back on the setup gate is swallowed too`() {
        // The gate reports itself as PLAYER: there is nothing behind it to go back to either.
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.CONSUME, press(d, back, 0, gestureEnabled = true))
    }

    @Test
    fun `back cancels the pin prompt on release`() {
        val d = MaintenanceKeyDispatcher()
        // The press is taken but does nothing: a BACK still held from the screen before must
        // not dismiss this prompt the instant it opens.
        assertEquals(KeyOutcome.CONSUME, press(d, back, 0, surface = KeySurface.PIN_PROMPT))
        assertEquals(
            KeyOutcome.CANCEL_PIN,
            press(d, back, 1, action = KeyEvent.ACTION_UP, surface = KeySurface.PIN_PROMPT)
        )
    }

    @Test
    fun `back falls through on the server setup screen which owns its own navigation`() {
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, back, 0, surface = KeySurface.SERVER_SETUP))
    }

    @Test
    fun `taking back does not disturb the gesture`() {
        // BACK is matched before the gesture buffer, so it must not reset or advance it.
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, up, 0))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, up, 1))
        assertEquals(KeyOutcome.CONSUME, press(d, back, 2))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, down, 3))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, down, 4))
        assertEquals(KeyOutcome.REVEAL_PIN, press(d, ok, 5))
    }

    @Test
    fun `the completing press reveals the pin prompt`() {
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, up, 0))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, up, 1))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, down, 2))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, down, 3))
        assertEquals(KeyOutcome.REVEAL_PIN, press(d, ok, 4))
    }

    @Test
    fun `the arrows fall through so the d-pad still moves focus`() {
        // If any of these were taken, the remote could not reach the second row.
        val d = MaintenanceKeyDispatcher()
        listOf(up to 0L, up to 1L, down to 2L, down to 3L).forEach { (key, at) ->
            assertEquals(KeyOutcome.PASS_THROUGH, press(d, key, at))
        }
    }

    @Test
    fun `a plain OK falls through so buttons still work`() {
        // The everyday case on the gate: someone presses OK to turn a switch on.
        val d = MaintenanceKeyDispatcher()
        repeat(4) { i ->
            assertEquals(KeyOutcome.PASS_THROUGH, press(d, ok, i * 100L))
        }
    }

    @Test
    fun `the release of a taken press is taken too`() {
        // Compose pairs a key down with its up. Letting the up through would click the
        // button the down was taken from -- opening Settings behind the pin prompt.
        val d = MaintenanceKeyDispatcher()
        listOf(up to 0L, up to 1L, down to 2L, down to 3L).forEach { (k, at) -> press(d, k, at) }
        assertEquals(KeyOutcome.REVEAL_PIN, press(d, ok, 4))
        assertEquals(KeyOutcome.CONSUME, press(d, ok, 5, action = KeyEvent.ACTION_UP))
        // ...and only that one release.
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, ok, 6, action = KeyEvent.ACTION_UP))
    }

    @Test
    fun `releases of keys we did not take always fall through`() {
        val d = MaintenanceKeyDispatcher()
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, ok, 0, action = KeyEvent.ACTION_UP))
        assertEquals(KeyOutcome.PASS_THROUGH, press(d, up, 1, action = KeyEvent.ACTION_UP))
    }

    @Test
    fun `a real down-up event stream still completes the gesture`() {
        // What a device actually sends: every press is a down and then an up. The ups must
        // not disturb the sequence the downs are building.
        val d = MaintenanceKeyDispatcher()
        var now = 0L
        var last = KeyOutcome.PASS_THROUGH
        for (key in listOf(up, up, down, down, ok)) {
            last = press(d, key, now++)
            press(d, key, now++, action = KeyEvent.ACTION_UP)
        }
        assertEquals(KeyOutcome.REVEAL_PIN, last)
    }

    @Test
    fun `a held key does not flood the sequence`() {
        val d = MaintenanceKeyDispatcher()
        press(d, up, 0)
        // Auto-repeat: these must not count as further presses.
        repeat(5) { i -> assertEquals(KeyOutcome.PASS_THROUGH, press(d, up, 1L + i, repeatCount = i + 1)) }
        press(d, up, 10)
        press(d, down, 11)
        press(d, down, 12)
        assertEquals(KeyOutcome.REVEAL_PIN, press(d, ok, 13))
    }

    @Test
    fun `nothing is taken on the pin prompt or the setup screen`() {
        // There these keys are someone's navigation, and a swallowed press mid-form is a
        // form that cannot be filled in.
        val d = MaintenanceKeyDispatcher()
        listOf(up, up, down, down, ok).forEachIndexed { i, key ->
            assertEquals(
                KeyOutcome.PASS_THROUGH,
                press(d, key, i.toLong(), gestureEnabled = false)
            )
        }
    }

    @Test
    fun `a slow sequence times out rather than matching`() {
        val d = MaintenanceKeyDispatcher()
        press(d, up, 0)
        press(d, up, 1)
        press(d, down, 2)
        press(d, down, 3)
        assertEquals(
            KeyOutcome.PASS_THROUGH,
            press(d, ok, MaintenanceGesture.WINDOW_MS + 100L)
        )
    }
}
