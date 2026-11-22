package com.goblin.launcher.data

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** This phone's token and its own PIN (never the vault PIN), encrypted with an Android Keystore key. */
class SecureStore(context: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        context, "goblin_secure",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(v) = prefs.edit().putString("token", v).apply()

    var phonePin: String
        get() = prefs.getString("phone_pin", "") ?: ""
        set(v) = prefs.edit().putString("phone_pin", v).apply()

    fun clear() = prefs.edit().clear().apply()
}
