package com.goblin.launcher.security

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import com.goblin.launcher.ui.theme.Border
import com.goblin.launcher.ui.theme.Brass
import com.goblin.launcher.ui.theme.TextMuted
import com.goblin.launcher.ui.theme.TextPrimary
import com.goblin.launcher.ui.theme.VaultBg

/**
 * Sign-in like root/su on device: biometric first, optional PIN dialog after.
 */
object SignInGate {
    /**
     * Full gate: biometric (or device credential) then optional PIN sheet callback.
     * @param requirePin if true, after biometric show PIN dialog and pass value to onSuccess
     */
    fun authenticate(
        activity: FragmentActivity,
        title: String = "Goblin Launcher",
        subtitle: String = "Confirm it's you",
        requirePin: Boolean = false,
        onNeedPinDialog: (() -> Unit)? = null,
        onSuccess: (pin: String?) -> Unit,
        onError: (String) -> Unit,
    ) {
        BiometricGate.authenticate(
            activity = activity,
            title = title,
            subtitle = subtitle,
            onSuccess = {
                if (requirePin) {
                    onNeedPinDialog?.invoke() ?: onSuccess(null)
                } else {
                    onSuccess(null)
                }
            },
            onError = onError,
        )
    }
}

@Composable
fun SignInPinDialog(
    title: String = "Sign in",
    hint: String = "Vault or mobile PIN",
    onConfirm: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var pin by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, color = Brass) },
        text = {
            Column {
                Text(
                    "Like a root prompt on device — enter PIN to continue.",
                    color = TextMuted,
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = pin,
                    onValueChange = { pin = it },
                    label = { Text(hint) },
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = Brass,
                        unfocusedBorderColor = Border,
                        focusedLabelColor = Brass,
                        unfocusedLabelColor = TextMuted,
                        cursorColor = Brass,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary,
                    ),
                )
            }
        },
        confirmButton = {
            Button(
                onClick = { if (pin.isNotBlank()) onConfirm(pin) },
                colors = ButtonDefaults.buttonColors(containerColor = Brass, contentColor = VaultBg),
            ) { Text("Sign in") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel", color = TextMuted) }
        },
        containerColor = VaultBg,
    )
}
