package com.goblin.launcher.data

import android.content.Context
import org.json.JSONObject
import java.io.File

/**
 * The pairing pushed from Goblin Launcher over USB:
 *   adb shell "run-as com.goblin.launcher sh -c 'cat > files/pairing.json'" < pairing
 * run-as writes into this app's PRIVATE storage (debuggable build); no other app can write there,
 * so nothing else can point this app at a fake server. Read once, then deleted. The phone PIN is
 * never in it: the owner types it once on the phone.
 */
object PairingImport {
    suspend fun consume(context: Context): Boolean {
        val f = File(context.filesDir, "pairing.json")
        if (!f.exists()) return false
        val j = runCatching { JSONObject(f.readText()) }.getOrNull()
        f.delete()
        if (j == null) return false
        val url = j.optString("url"); val cert = j.optString("cert"); val token = j.optString("token")
        if (url.isBlank() || cert.isBlank() || token.isBlank()) return false
        Prefs(context).save(Pairing(url, cert, j.optString("link"), j.optString("machine"),
            j.optString("ssh_user"), j.optString("ssh_host")))
        val secure = SecureStore(context)
        secure.token = token
        secure.phonePin = ""                 // a new pairing needs its phone PIN typed again
        return true
    }
}
