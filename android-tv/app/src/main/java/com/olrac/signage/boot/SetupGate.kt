package com.olrac.signage.boot

/**
 * The device switches a TV needs before the player can hold its screen unattended, and the
 * rules the setup gate follows while they are still off.
 *
 * Kept free of Android types so the ordering and focus rules below can be tested on the JVM.
 * Reading the switches themselves is the caller's job -- see [WatchdogStatus] and
 * `Settings.canDrawOverlays`.
 */
enum class SetupRequirement {
    /** SYSTEM_ALERT_WINDOW: what lets the player pull itself to the front after boot. */
    OVERLAY,

    /** The watchdog accessibility service: what puts the player back if something covers it. */
    WATCHDOG,

    /** REQUEST_INSTALL_PACKAGES: what lets the TV install player updates unattended. */
    AUTO_UPDATE
}

data class SetupRequirementState(
    val requirement: SetupRequirement,
    val granted: Boolean
)

object SetupGate {

    /** The order the gate lists them in: the one that gets a TV playing at all comes first. */
    val ORDER = listOf(SetupRequirement.OVERLAY, SetupRequirement.WATCHDOG, SetupRequirement.AUTO_UPDATE)

    /** The gate closes itself -- and never asks again -- only once every switch is on. */
    fun isSatisfied(states: List<SetupRequirementState>): Boolean =
        states.isNotEmpty() && states.all { it.granted }

    /**
     * Which row the remote should land on when the gate opens: the first switch still off,
     * because that is the one the installer has to act on.
     *
     * Falls back to the first row rather than "none" when everything is already granted. A
     * gate in that state is closing anyway, and returning an index that addresses no row is
     * how a screen ends up with nothing focused -- which is exactly the state that leaves a
     * D-pad remote with nowhere to go.
     */
    fun initialFocusIndex(states: List<SetupRequirementState>): Int =
        states.indexOfFirst { !it.granted }.takeIf { it >= 0 } ?: 0

    /**
     * Does [setting] -- the raw colon-separated ENABLED_ACCESSIBILITY_SERVICES value -- name
     * the service `packageName/className`?
     *
     * Android writes the component in SHORT form, `pkg/.Inner.Class`, whenever the class sits
     * inside the package, and that is what the televisions actually store:
     *
     *     com.olrac.signage/.boot.WatchdogAccessibilityService
     *
     * The check compared against the LONG form, `pkg/pkg.Inner.Class`, so it never matched.
     * The app therefore read its own running watchdog as switched off: the setup gate came
     * back on every single restart however many times the installer turned the switch on, the
     * TV never reached the adverts, the 1 Hz gate poll never stopped, and every fleet report
     * said watchdog_enabled=false. Confirmed on the Lenovo panel, where dumpsys shows the
     * service bound while the setting holds the short spelling.
     *
     * Both spellings are normalised before comparing, so either is accepted.
     */
    fun namesService(setting: String?, packageName: String, className: String): Boolean {
        val wanted = normaliseComponent("$packageName/$className") ?: return false
        return (setting ?: "").split(':').any { entry ->
            normaliseComponent(entry.trim())?.equals(wanted, ignoreCase = true) == true
        }
    }

    /** "pkg/.A.B" and "pkg/pkg.A.B" both become "pkg/pkg.A.B". Null when not a component. */
    private fun normaliseComponent(entry: String): String? {
        val slash = entry.indexOf('/')
        if (slash <= 0 || slash == entry.length - 1) return null
        val pkg = entry.substring(0, slash)
        val cls = entry.substring(slash + 1)
        return if (cls.startsWith(".")) "$pkg/$pkg$cls" else "$pkg/$cls"
    }
}
