package com.goblin.launcher.data

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

class ApiException(message: String, val code: Int? = null) : Exception(message)

data class VaultStatus(
    val unlocked: Boolean,
    val initialized: Boolean,
    val secondsRemaining: Int,
    val canExtend: Boolean,
    val method: String?,
)

data class Approval(val id: String, val agent: String, val target: String, val summary: String, val ageS: Int)

/**
 * Goblin Launcher's phone listener (HTTPS on the LAN / WireGuard). The ONLY certificate accepted
 * is the one whose SHA-256 (DER) matches the paired pin: no CA, no user-added CAs. The token is
 * this phone's own; the vault lets it unlock, extend, lock and decide approvals, nothing else.
 */
class GoblinApi(private var baseUrl: String = "", private var token: String = "", private var certPin: String = "") {
    private var client = build(certPin)

    fun update(baseUrl: String, token: String, certPin: String) {
        this.baseUrl = baseUrl.trim().trimEnd('/')
        this.token = token.trim()
        val pin = certPin.trim().lowercase().replace(":", "")
        if (pin != this.certPin) {
            this.certPin = pin
            client = build(pin)
        }
    }

    private fun build(pin: String): OkHttpClient {
        val b = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(150, TimeUnit.SECONDS)          // an approval waits for a human
        if (pin.isBlank()) return b.build()
        val tm = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
                throw CertificateException("client certificates are not used")
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                val leaf = chain.firstOrNull() ?: throw CertificateException("no server certificate")
                val got = MessageDigest.getInstance("SHA-256").digest(leaf.encoded).joinToString("") { "%02x".format(it) }
                if (got != pin) throw CertificateException("this isn't the paired computer (certificate changed)")
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf<TrustManager>(tm), null)
        return b.sslSocketFactory(ctx.socketFactory, tm).hostnameVerifier { _, _ -> true }.build()
    }

    fun health(): JSONObject = call("GET", "/health")

    fun status(): VaultStatus {
        val j = call("GET", "/status")
        return VaultStatus(j.optBoolean("unlocked"), j.optBoolean("initialized"), j.optInt("seconds_remaining"),
            j.optBoolean("can_extend"), j.optString("method").ifBlank { null })
    }

    /** The vault still knows this phone and its USB key (and the key's host copy is present). */
    fun check(): JSONObject = call("POST", "/phone/check", JSONObject())

    fun unlockWithPhonePin(phonePin: String) = call("POST", "/phone/unlock", JSONObject().put("phone_pin", phonePin))

    fun unlockWithVaultPin(vaultPin: String) = call("POST", "/unlock", JSONObject().put("pin", vaultPin))

    fun extend(phonePin: String, minutes: Int = 15) =
        call("POST", "/extend", JSONObject().put("phone_pin", phonePin).put("minutes", minutes).put("mode", "add"))

    fun lock() = call("POST", "/lock", JSONObject())

    fun approvals(): List<Approval> {
        val arr: JSONArray = call("GET", "/approvals").optJSONArray("pending") ?: JSONArray()
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            Approval(o.optString("id"), o.optString("agent"), o.optString("target_id"), o.optString("summary"), o.optInt("age_s"))
        }
    }

    fun decide(id: String, approve: Boolean, phonePin: String) {
        val body = JSONObject().put("approve", approve)
        if (approve) body.put("phone_pin", phonePin)
        call("POST", "/approvals/$id", body)
    }

    private fun call(method: String, path: String, body: JSONObject? = null): JSONObject {
        if (baseUrl.isBlank()) throw ApiException("Not paired yet: add this phone from the launcher's Workshop")
        val rb = Request.Builder().url(baseUrl + path)
        if (token.isNotBlank()) rb.header("Authorization", "Bearer $token")
        if (method == "POST") rb.post((body ?: JSONObject()).toString().toRequestBody("application/json".toMediaType()))
        client.newCall(rb.build()).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) {
                val msg = runCatching { JSONObject(text).optString("error") }.getOrNull().orEmpty()
                throw ApiException(msg.ifBlank { "HTTP ${r.code}" }, r.code)
            }
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        }
    }
}
