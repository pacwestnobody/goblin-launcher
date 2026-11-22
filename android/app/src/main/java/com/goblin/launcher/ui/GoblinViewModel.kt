package com.goblin.launcher.ui

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.goblin.launcher.data.Approval
import com.goblin.launcher.data.GoblinApi
import com.goblin.launcher.data.Pairing
import com.goblin.launcher.data.PairingImport
import com.goblin.launcher.data.Prefs
import com.goblin.launcher.data.SecureStore
import com.goblin.launcher.data.VaultStatus
import com.goblin.launcher.security.BiometricGate
import com.goblin.launcher.termux.TermuxHelper
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class UiState(
    val pairing: Pairing = Pairing(),
    val needsPhonePin: Boolean = false,
    val status: VaultStatus? = null,
    val reachable: Boolean? = null,
    val linked: Boolean? = null,          // the vault still knows this phone and its USB key
    val linkDetail: String = "",
    val approvals: List<Approval> = emptyList(),
    val termux: Boolean = false,
    val busy: Boolean = false,
    val message: String = "",
    val error: String = "",
    val askVaultPin: Boolean = false,
)

class GoblinViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = Prefs(app)
    private val secure = SecureStore(app)
    private val api = GoblinApi()
    private val ready = CompletableDeferred<Unit>()
    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui.asStateFlow()

    init {
        viewModelScope.launch {
            prefs.pairing.collect { p ->
                api.update(p.url, secure.token, p.certPin)
                ready.complete(Unit)
                _ui.update { it.copy(pairing = p, needsPhonePin = p.paired && secure.phonePin.isBlank()) }
            }
        }
        refreshTermux()
        checkPairing()
        refresh()
        viewModelScope.launch {
            while (true) {                       // approvals show while the app is open
                if (_ui.value.pairing.paired) loadApprovals()
                delay(4000)
            }
        }
    }

    fun checkPairing() = viewModelScope.launch {
        if (withContext(Dispatchers.IO) { PairingImport.consume(getApplication()) }) {
            _ui.update { it.copy(message = "Paired. Type the phone PIN you chose in the Workshop.",
                                 error = "", needsPhonePin = true) }
            refresh()
        }
    }

    fun setPhonePin(pin: String, again: String) {
        when {
            !Regex("\\d{4,12}").matches(pin) -> _ui.update { it.copy(error = "The phone PIN is 4 to 12 digits") }
            pin != again -> _ui.update { it.copy(error = "The two PINs don't match") }
            else -> {
                secure.phonePin = pin
                _ui.update { it.copy(needsPhonePin = false, message = "Phone PIN saved (encrypted on this phone)", error = "") }
            }
        }
    }

    fun refreshTermux() = _ui.update { it.copy(termux = TermuxHelper.status(getApplication()).termuxInstalled) }

    fun refresh() = run("Checking…") {
        try {
            api.health()
            _ui.update { it.copy(reachable = true) }
        } catch (e: Exception) {
            _ui.update { it.copy(reachable = false) }
            throw e
        }
        try {
            val c = api.check()
            _ui.update { it.copy(linked = c.optBoolean("bound"), linkDetail = c.optString("device_label")) }
        } catch (e: Exception) {
            _ui.update { it.copy(linked = false, linkDetail = e.message ?: "not linked") }
        }
        val st = api.status()
        _ui.update { it.copy(status = st, message = if (st.unlocked) "Vault open · ${mmss(st.secondsRemaining)} left" else "Vault sealed") }
    }

    private fun loadApprovals() = viewModelScope.launch {
        runCatching { ready.await(); withContext(Dispatchers.IO) { api.approvals() } }
            .onSuccess { list -> _ui.update { it.copy(approvals = list) } }
    }

    private fun phonePinOr(err: String): String? =
        secure.phonePin.ifBlank { _ui.update { it.copy(error = err) }; null }

    fun approve(activity: FragmentActivity, id: String) {
        val pin = phonePinOr("Type your phone PIN once first") ?: return
        biometric(activity, "Approve this request") {
            run("Stamping…") { api.decide(id, true, pin); dropApproval(id, "Stamped") }
        }
    }

    fun deny(id: String) = run("Refusing…") { api.decide(id, false, ""); dropApproval(id, "Refused") }

    private fun dropApproval(id: String, msg: String) =
        _ui.update { s -> s.copy(message = msg, approvals = s.approvals.filter { it.id != id }) }

    fun unlockWithPhone(activity: FragmentActivity) {
        val pin = phonePinOr("Type your phone PIN once first") ?: return
        biometric(activity, "Open the vault") { run("Turning the wheel…") { api.unlockWithPhonePin(pin); after("Vault open") } }
    }

    fun askVaultPin(activity: FragmentActivity) = biometric(activity, "Open the vault with its PIN") {
        _ui.update { it.copy(askVaultPin = true) }
    }

    fun vaultPinEntered(pin: String) {
        _ui.update { it.copy(askVaultPin = false) }
        run("Turning the wheel…") { api.unlockWithVaultPin(pin); after("Vault open") }
    }

    fun dismissVaultPin() = _ui.update { it.copy(askVaultPin = false) }

    fun extend(activity: FragmentActivity) {
        val pin = phonePinOr("Type your phone PIN once first") ?: return
        biometric(activity, "Wind 15 more minutes") { run("Winding…") { api.extend(pin); after("15 more minutes") } }
    }

    fun lock() = run("Sealing…") { api.lock(); after("Vault sealed") }

    private fun after(msg: String) {
        val st = api.status()
        _ui.update { it.copy(status = st, message = msg) }
    }

    // ---- Termux: the lab menu and the room over SSH (the server's key only allows those) ----------------
    private fun sshTarget(): String? {
        val p = _ui.value.pairing
        val host = p.sshHost.ifBlank { p.url.substringAfter("://").substringBefore(":") }
        if (host.isBlank()) return null
        return if (p.sshUser.isBlank()) host else "${p.sshUser}@$host"
    }

    fun openLabMenu(context: Context) = termux(context, "lab menu", "ssh -t %s menu")

    fun openRoom(context: Context) = termux(context, "room", "ssh %s room read 10; echo; echo 'room post TEXT | room read 20 | room wait'; exec bash -i")

    private fun termux(context: Context, what: String, template: String) {
        val target = sshTarget() ?: return _ui.update { it.copy(error = "Pair the phone first") }
        if (!TermuxHelper.ensureTermux(context)) return _ui.update { it.copy(error = "Install Termux first (F-Droid)") }
        val cmd = template.format(target)
        if (!TermuxHelper.runInTermux(context, cmd, background = false)) {
            // Play-store Termux has no RUN_COMMAND: copy the command, open Termux, the owner pastes it
            (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                .setPrimaryClip(ClipData.newPlainText(what, cmd))
            TermuxHelper.openTermux(context)
            _ui.update { it.copy(message = "Copied the $what command. In Termux: long-press, Paste, Enter.") }
        }
    }

    // ---- plumbing -----------------------------------------------------------------------
    private fun biometric(activity: FragmentActivity, why: String, block: () -> Unit) = activity.runOnUiThread {
        BiometricGate.authenticate(activity, title = "Goblin Launcher", subtitle = why, onSuccess = { block() },
            onError = { msg -> _ui.update { it.copy(error = "Fingerprint needed: $msg", message = "") } })
    }

    private fun run(busy: String, block: suspend () -> Unit) {
        viewModelScope.launch {
            _ui.update { it.copy(busy = true, error = "", message = busy) }
            try {
                ready.await()
                withContext(Dispatchers.IO) { block() }
            } catch (e: Exception) {
                _ui.update { it.copy(error = e.message ?: "Something went wrong", message = "") }
            } finally {
                _ui.update { it.copy(busy = false) }
            }
        }
    }

    private fun mmss(s: Int) = "%d:%02d".format(s / 60, s % 60)
}
