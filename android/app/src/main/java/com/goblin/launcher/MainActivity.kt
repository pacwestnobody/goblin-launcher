package com.goblin.launcher

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.fragment.app.FragmentActivity
import com.goblin.launcher.termux.TermuxHelper
import com.goblin.launcher.ui.Actions
import com.goblin.launcher.ui.GoblinViewModel
import com.goblin.launcher.ui.HomeScreen
import com.goblin.launcher.ui.theme.GoblinTheme
import com.goblin.launcher.ui.theme.VaultBg

/** FragmentActivity: BiometricPrompt needs it. */
class MainActivity : FragmentActivity() {
    private val vm: GoblinViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val a = Actions(
            refresh = { vm.refreshTermux(); vm.refresh() },
            unlockWithPhone = { vm.unlockWithPhone(this) },
            askVaultPin = { vm.askVaultPin(this) },
            vaultPinEntered = vm::vaultPinEntered,
            dismissVaultPin = vm::dismissVaultPin,
            extend = { vm.extend(this) },
            lock = { vm.lock() },
            approve = { id -> vm.approve(this, id) },
            deny = { id -> vm.deny(id) },
            setPhonePin = vm::setPhonePin,
            labMenu = { vm.openLabMenu(this) },
            room = { vm.openRoom(this) },
            installTermux = { TermuxHelper.ensureTermux(this); vm.refreshTermux() },
        )
        setContent {
            GoblinTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = VaultBg) {
                    val state by vm.ui.collectAsState()
                    HomeScreen(state, a)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        vm.checkPairing()
        vm.refreshTermux()
    }
}
