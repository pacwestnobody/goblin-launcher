package com.goblin.launcher.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore("goblin_prefs")

/** Where the paired computer is and how to reach it. Secrets (token, phone PIN) live in SecureStore. */
data class Pairing(
    val url: String = "",
    val certPin: String = "",
    val linkId: String = "",
    val machine: String = "",
    val sshUser: String = "",
    val sshHost: String = "",
) {
    val paired: Boolean get() = url.isNotBlank() && certPin.isNotBlank()
}

class Prefs(private val context: Context) {
    private val k = mapOf(
        "url" to stringPreferencesKey("url"), "cert" to stringPreferencesKey("cert"),
        "link" to stringPreferencesKey("link"), "machine" to stringPreferencesKey("machine"),
        "ssh_user" to stringPreferencesKey("ssh_user"), "ssh_host" to stringPreferencesKey("ssh_host"),
    )

    val pairing: Flow<Pairing> = context.dataStore.data.map { p ->
        Pairing(p[k.getValue("url")] ?: "", p[k.getValue("cert")] ?: "", p[k.getValue("link")] ?: "",
            p[k.getValue("machine")] ?: "", p[k.getValue("ssh_user")] ?: "", p[k.getValue("ssh_host")] ?: "")
    }

    suspend fun save(pair: Pairing) {
        context.dataStore.edit { p ->
            p[k.getValue("url")] = pair.url.trim().trimEnd('/')
            p[k.getValue("cert")] = pair.certPin.trim().lowercase().replace(":", "")
            p[k.getValue("link")] = pair.linkId
            p[k.getValue("machine")] = pair.machine
            p[k.getValue("ssh_user")] = pair.sshUser
            p[k.getValue("ssh_host")] = pair.sshHost
        }
    }

    suspend fun clear() = context.dataStore.edit { it.clear() }
}
