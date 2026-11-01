package com.olrac.signage.sync

/**
 * Merges the two shapes a sync or heartbeat response can carry remote commands in.
 *
 * `pending_commands` holds every command still within its hold time -- a screen queued
 * more than one while it was offline -- and `pending_command` is only its first entry,
 * kept so this still works against a server that has not been upgraded yet. Deduplicated
 * because an upgraded server sends the same command in both fields.
 */
object PendingCommands {
    fun merge(primary: String?, list: List<String>?): List<String> =
        (listOfNotNull(primary) + (list ?: emptyList())).distinct()
}
