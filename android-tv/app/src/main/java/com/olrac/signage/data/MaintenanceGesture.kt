package com.olrac.signage.data

import android.view.KeyEvent

/**
 * The remote-control gesture that reveals the maintenance screen: Up, Up, Down, Down, OK
 * inside [WINDOW_MS].
 *
 * Deliberately not prefixed with Back. Back is exactly what someone presses when they want
 * out of a fullscreen app, so any sequence starting with it is the one most likely to fire
 * by accident on a public screen. Every key used here exists on every TV remote — media
 * keys do not, which is why the previous sequence needed two variants to cover the same
 * hardware.
 *
 * Secrecy is not the control: the pin on the screen behind this is. The gesture only has to
 * be something nobody produces by accident.
 */
class MaintenanceGesture(private val windowMs: Long = WINDOW_MS) {
    private val pressed = ArrayDeque<Pair<Long, Int>>()

    /** Returns true when [keyCode] completes the sequence. */
    fun record(keyCode: Int, nowMs: Long): Boolean {
        pressed.addLast(nowMs to normalize(keyCode))

        // Presses too old to count, then anything before the last SEQUENCE.size keys.
        while (pressed.isNotEmpty() && nowMs - pressed.first().first > windowMs) {
            pressed.removeFirst()
        }
        while (pressed.size > SEQUENCE.size) {
            pressed.removeFirst()
        }

        if (pressed.map { it.second } != SEQUENCE) return false
        pressed.clear()
        return true
    }

    /** Remotes disagree on which code the OK button sends; all of these mean confirm. */
    private fun normalize(keyCode: Int): Int =
        if (keyCode == KeyEvent.KEYCODE_ENTER || keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER) {
            KeyEvent.KEYCODE_DPAD_CENTER
        } else {
            keyCode
        }

    companion object {
        const val WINDOW_MS = 5_000L

        val SEQUENCE = listOf(
            KeyEvent.KEYCODE_DPAD_UP,
            KeyEvent.KEYCODE_DPAD_UP,
            KeyEvent.KEYCODE_DPAD_DOWN,
            KeyEvent.KEYCODE_DPAD_DOWN,
            KeyEvent.KEYCODE_DPAD_CENTER
        )
    }
}
