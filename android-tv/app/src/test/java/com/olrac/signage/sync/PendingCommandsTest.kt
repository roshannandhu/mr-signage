package com.olrac.signage.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class PendingCommandsTest {

    @Test
    fun `an upgraded server's list and an old server's single field both come through`() {
        assertEquals(listOf("bring_to_front", "remote_unlock"),
            PendingCommands.merge("bring_to_front", listOf("bring_to_front", "remote_unlock")))
    }

    @Test
    fun `a server that only sends the single field still works`() {
        assertEquals(listOf("check_update"), PendingCommands.merge("check_update", null))
    }

    @Test
    fun `nothing queued is an empty list, not null`() {
        assertEquals(emptyList<String>(), PendingCommands.merge(null, null))
        assertEquals(emptyList<String>(), PendingCommands.merge(null, emptyList()))
    }
}
