package com.olrac.signage

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins which components other apps can reach. The install-result receiver was exported with
 * no permission, so any app could hand it an Intent to start under this app's identity.
 */
class ManifestExposureTest {

    private val manifest: String by lazy {
        // Unit tests run with the module directory as the working directory.
        listOf("src/main/AndroidManifest.xml", "app/src/main/AndroidManifest.xml")
            .map { File(it) }
            .first { it.exists() }
            .readText()
    }

    private fun tagFor(componentName: String): String {
        val start = manifest.indexOf("android:name=\"$componentName\"")
        assertTrue("$componentName is not declared", start >= 0)
        val open = manifest.lastIndexOf('<', start)
        val close = manifest.indexOf('>', start)
        return manifest.substring(open, close + 1)
    }

    @Test
    fun `the install result receiver is not exported`() {
        val tag = tagFor(".sync.InstallReceiver")
        assertTrue(tag, tag.contains("android:exported=\"false\""))
    }

    @Test
    fun `the device admin receiver stays protected by its permission`() {
        val tag = tagFor(".receivers.SignageDeviceAdminReceiver")
        assertTrue(tag, tag.contains("android.permission.BIND_DEVICE_ADMIN"))
    }
}
