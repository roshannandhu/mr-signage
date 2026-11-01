package com.olrac.signage.data

import android.view.KeyEvent

/** What the Activity should do with a key event it is dispatching. */
enum class KeyOutcome {
    /** Hand it to the view tree as normal -- focus moves, buttons click. */
    PASS_THROUGH,

    /** Take it and do nothing else. */
    CONSUME,

    /** Take it and reveal the maintenance pin prompt. Implies consuming. */
    REVEAL_PIN,

    /** Take it and dismiss the maintenance pin prompt. Implies consuming. */
    CANCEL_PIN
}

/** Which surface is in front, for the keys whose meaning depends on it. */
enum class KeySurface {
    /** The ads, or the setup gate: neither has anywhere to go BACK to. */
    PLAYER,

    /** The maintenance pin prompt: BACK is its only way out on a remote. */
    PIN_PROMPT,

    /** The server setup screen, which owns its own BACK navigation. */
    SERVER_SETUP
}

/**
 * The decision half of the Activity's `dispatchKeyEvent`, kept free of Activity state so the
 * rules below can be tested on the JVM rather than reasoned about.
 *
 * It runs ahead of the view tree, which is the whole point: `onKeyDown` only sees keys
 * nothing consumed, and a focused Button consumes OK. Over the player that never mattered --
 * there is nothing focusable behind a video -- but on a screen with buttons the final OK of
 * the gesture went to whichever button held focus, so a TV whose switches are both off could
 * not be serviced at all.
 *
 * Running first is also what makes it dangerous, so it takes as little as it can: every key
 * falls through except the one press that COMPLETES the sequence, plus that press's own
 * release. Anything more and the arrows stop moving focus, or OK stops working on buttons.
 */
class MaintenanceKeyDispatcher(
    private val gesture: MaintenanceGesture = MaintenanceGesture()
) {
    private var swallowUpFor: Int? = null

    /**
     * @param gestureEnabled false on the surfaces where these keys are someone's navigation
     *   rather than a gesture -- the pin prompt and the setup screen -- so a press mid-form
     *   is never swallowed.
     * @param kioskLocked false when the tenant's plan does not sell the TV lock feature (or
     *   has turned it off): BACK over the player is then someone's ordinary navigation, not
     *   a kiosk to escape, and passes straight through with no PIN involved. True is the
     *   safe default an unrecognised or pre-3.10 caller gets.
     */
    fun onKeyEvent(
        action: Int,
        keyCode: Int,
        repeatCount: Int,
        gestureEnabled: Boolean,
        nowMs: Long,
        surface: KeySurface = KeySurface.PLAYER,
        kioskLocked: Boolean = true
    ): KeyOutcome {
        // BACK was handled nowhere, so the default finished the Activity and left the viewer
        // on the TV's launcher -- the one thing kiosk exists to prevent, and reachable without
        // the maintenance gesture. It is taken everywhere except the screen that owns it --
        // and except the player itself once the plan has turned kiosk lock off, where BACK
        // leaving IS the point rather than the thing being guarded against.
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            return when (surface) {
                KeySurface.SERVER_SETUP -> KeyOutcome.PASS_THROUGH
                // Act on the release, so a BACK still held from the previous screen cannot
                // dismiss this one the instant it opens.
                KeySurface.PIN_PROMPT ->
                    if (action == KeyEvent.ACTION_UP) KeyOutcome.CANCEL_PIN else KeyOutcome.CONSUME
                KeySurface.PLAYER -> if (kioskLocked) KeyOutcome.CONSUME else KeyOutcome.PASS_THROUGH
            }
        }

        if (action == KeyEvent.ACTION_UP && swallowUpFor == keyCode) {
            // The release belonging to a press we already took. Compose pairs a key down with
            // its up, so letting this through would still click the button underneath.
            swallowUpFor = null
            return KeyOutcome.CONSUME
        }

        // Auto-repeat from a held key would otherwise flood the gesture buffer.
        if (action == KeyEvent.ACTION_DOWN && gestureEnabled && repeatCount == 0) {
            if (gesture.record(keyCode, nowMs)) {
                swallowUpFor = keyCode
                return KeyOutcome.REVEAL_PIN
            }
        }

        return KeyOutcome.PASS_THROUGH
    }
}
