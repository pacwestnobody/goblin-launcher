package com.goblin.launcher.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.goblin.launcher.R
import com.goblin.launcher.data.Approval
import com.goblin.launcher.security.SignInPinDialog
import com.goblin.launcher.ui.theme.Alegreya
import com.goblin.launcher.ui.theme.Brass
import com.goblin.launcher.ui.theme.BrassDim
import com.goblin.launcher.ui.theme.Copper
import com.goblin.launcher.ui.theme.Danger
import com.goblin.launcher.ui.theme.Ember
import com.goblin.launcher.ui.theme.Fell
import com.goblin.launcher.ui.theme.Glow
import com.goblin.launcher.ui.theme.Ink
import com.goblin.launcher.ui.theme.Iron
import com.goblin.launcher.ui.theme.Muted
import com.goblin.launcher.ui.theme.Paper
import com.goblin.launcher.ui.theme.Patina
import com.goblin.launcher.ui.theme.Plate
import com.goblin.launcher.ui.theme.PlateHi
import com.goblin.launcher.ui.theme.Typewriter

class Actions(
    val refresh: () -> Unit,
    val unlockWithPhone: () -> Unit,
    val askVaultPin: () -> Unit,
    val vaultPinEntered: (String) -> Unit,
    val dismissVaultPin: () -> Unit,
    val extend: () -> Unit,
    val lock: () -> Unit,
    val approve: (String) -> Unit,
    val deny: (String) -> Unit,
    val setPhonePin: (String, String) -> Unit,
    val labMenu: () -> Unit,
    val room: () -> Unit,
    val installTermux: () -> Unit,
)

@Composable
fun HomeScreen(s: UiState, a: Actions) {
    if (s.askVaultPin) {
        SignInPinDialog(title = "Vault PIN", hint = "Vault PIN", onConfirm = a.vaultPinEntered, onDismiss = a.dismissVaultPin)
    }
    Column(
        Modifier.fillMaxSize().background(Iron)
            .background(Brush.radialGradient(listOf(Ember, Color.Transparent), Offset(300f, -80f), 1100f))
            .verticalScroll(rememberScrollState()).statusBarsPadding().navigationBarsPadding()
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Header(s)
        if (!s.pairing.paired) NotPaired()
        if (s.needsPhonePin) PhonePinSetup(a.setPhonePin)
        if (s.pairing.paired) {
            TubePlate(s.approvals, s.busy, a.approve, a.deny)
            VaultPlate(s, a)
        }
        Messages(s)
        if (s.pairing.paired) GaugesPlate(s, a.refresh)
        TermuxPlate(s, a)
        Text("Talks only to ${s.pairing.machine.ifBlank { "your computer" }}, over a pinned certificate, from the LAN or WireGuard.",
            color = Muted, fontFamily = Typewriter, fontSize = 11.sp, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
    }
}

/* ---------- machinery ---------- */

@Composable
private fun RivetPlate(title: String? = null, rim: Color = BrassDim, content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxWidth().shadow(8.dp, RoundedCornerShape(6.dp)).clip(RoundedCornerShape(6.dp))
            .background(Brush.verticalGradient(listOf(PlateHi, Plate, Plate)))
            .border(2.dp, rim, RoundedCornerShape(6.dp))
            .drawBehind {
                val r = 3.dp.toPx(); val i = 9.dp.toPx()
                listOf(Offset(i, i), Offset(size.width - i, i), Offset(i, size.height - i), Offset(size.width - i, size.height - i)).forEach {
                    drawCircle(Color.Black, r + 1.2f, it + Offset(0.8f, 0.8f))
                    drawCircle(BrassDim, r, it)
                    drawCircle(Brass.copy(alpha = .6f), r * .45f, it - Offset(r * .3f, r * .3f))
                }
            }
            .padding(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (title != null) {
            Text(title, fontFamily = Fell, fontSize = 20.sp, color = Brass)
            Box(Modifier.fillMaxWidth().height(1.dp).background(Brush.horizontalGradient(listOf(BrassDim, Color.Transparent))))
        }
        content()
    }
}

@Composable
private fun Header(s: UiState) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        Image(painterResource(R.drawable.goblin), contentDescription = "The goblin", contentScale = ContentScale.Crop,
            modifier = Modifier.size(72.dp).clip(CircleShape).border(3.dp, Brass, CircleShape))
        Column {
            Text("Goblin Launcher", fontFamily = Fell, fontSize = 30.sp, color = Brass, lineHeight = 32.sp)
            Text((s.pairing.machine.ifBlank { "not paired" }).lowercase() + " · vault & bridge",
                fontFamily = Typewriter, fontSize = 12.sp, color = Muted)
        }
    }
}

@Composable
private fun Lamp(on: Boolean?, size: Dp = 12.dp) {
    val c = when (on) { true -> Patina; false -> Danger; null -> BrassDim }
    Box(Modifier.size(size).shadow(if (on == null) 0.dp else 6.dp, CircleShape, ambientColor = c, spotColor = c)
        .clip(CircleShape).background(Brush.radialGradient(listOf(c, c.copy(alpha = .55f)))).border(1.5.dp, BrassDim, CircleShape))
}

@Composable
private fun BrassButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier.fillMaxWidth(),
                        enabled: Boolean = true, color: Color = Brass) {
    Button(onClick = onClick, enabled = enabled, modifier = modifier, shape = RoundedCornerShape(4.dp),
        colors = ButtonDefaults.buttonColors(containerColor = color, contentColor = Iron,
            disabledContainerColor = BrassDim.copy(alpha = .4f), disabledContentColor = Muted)) {
        Text(text, fontFamily = Fell, fontSize = 17.sp)
    }
}

@Composable
private fun IronButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier.fillMaxWidth(),
                       enabled: Boolean = true, color: Color = Ink) {
    OutlinedButton(onClick = onClick, enabled = enabled, modifier = modifier, shape = RoundedCornerShape(4.dp),
        border = BorderStroke(1.5.dp, BrassDim),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = color, disabledContentColor = Muted)) {
        Text(text, fontFamily = Fell, fontSize = 16.sp)
    }
}

@Composable
private fun Hint(text: String) = Text(text, color = Muted, fontSize = 12.sp, fontFamily = Alegreya)

/* ---------- plates ---------- */

@Composable
private fun NotPaired() = RivetPlate(title = "Not paired yet", rim = Glow) {
    Hint("On the computer, open Goblin Launcher, unlock the vault, and go to Workshop → Phones → Add a phone. " +
        "It walks you through plugging this phone in and installs the pairing here. Nothing to type on the phone.")
}

@Composable
private fun TubePlate(items: List<Approval>, busy: Boolean, onApprove: (String) -> Unit, onDeny: (String) -> Unit) {
    RivetPlate(title = "Pneumatic tube", rim = if (items.isEmpty()) BrassDim else Glow) {
        if (items.isEmpty()) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Lamp(null)
                Text("Nothing in the tube. The goblins are behaving.", fontFamily = Typewriter, fontSize = 13.sp, color = Muted)
            }
        }
        for (x in items) {
            Column(Modifier.fillMaxWidth().shadow(4.dp, RoundedCornerShape(2.dp)).background(Paper, RoundedCornerShape(2.dp)).padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("REQUEST · ${x.target.uppercase()}", fontFamily = Typewriter, fontSize = 11.sp, color = Copper)
                Text("${x.agent} wants to run", fontFamily = Typewriter, fontSize = 15.sp, color = Color(0xFF221A10))
                Text(x.summary, fontFamily = Typewriter, fontSize = 13.sp, color = Color(0xFF4A3C2A))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    BrassButton("Stamp it", { onApprove(x.id) }, Modifier.weight(1f), enabled = !busy, color = Patina)
                    BrassButton("Refuse", { onDeny(x.id) }, Modifier.weight(1f), enabled = !busy, color = Danger)
                }
            }
        }
    }
}

@Composable
private fun VaultPlate(s: UiState, a: Actions) {
    val open = s.status?.unlocked == true
    RivetPlate(title = "The vault", rim = if (open) Glow else BrassDim) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            val grey = ColorMatrix().apply { setToSaturation(.35f) }
            Image(painterResource(R.drawable.door), contentDescription = "Vault door", colorFilter = if (open) null else ColorFilter.colorMatrix(grey),
                modifier = Modifier.size(84.dp).clip(CircleShape).border(2.dp, if (open) Glow else BrassDim, CircleShape))
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(when { s.status == null -> "Unknown"; open -> "Open"; else -> "Sealed" }, fontFamily = Fell, fontSize = 28.sp,
                    color = if (open) Glow else Ink)
                val left = s.status?.secondsRemaining ?: 0
                Text(if (open) "closes in %d:%02d".format(left / 60, left % 60) else "phone PIN or vault PIN to open",
                    fontFamily = Typewriter, fontSize = 13.sp, color = Muted)
            }
        }
        if (!open) {
            BrassButton("Turn the wheel", a.unlockWithPhone, enabled = !s.busy && !s.needsPhonePin)
            IronButton("Use the vault PIN instead", a.askVaultPin, enabled = !s.busy, color = Brass)
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                BrassButton("Wind +15 min", a.extend, Modifier.weight(1f), enabled = !s.busy && s.status?.canExtend == true)
                IronButton("Seal it", a.lock, Modifier.weight(1f), enabled = !s.busy, color = Danger)
            }
        }
    }
}

@Composable
private fun Messages(s: UiState) {
    if (s.message.isNotBlank()) Text("✦ " + s.message, color = Patina, fontFamily = Typewriter, fontSize = 13.sp)
    if (s.error.isNotBlank()) Text("✖ " + s.error, color = Danger, fontFamily = Typewriter, fontSize = 13.sp)
    if (s.busy) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(color = Glow, strokeWidth = 2.dp, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(10.dp))
            Text("Gears turning…", color = Muted, fontFamily = Typewriter, fontSize = 13.sp)
        }
    }
}

@Composable
private fun GaugesPlate(s: UiState, onRefresh: () -> Unit) = RivetPlate(title = "Gauges") {
    GaugeRow(s.pairing.machine.ifBlank { "Computer" }, s.reachable,
        when (s.reachable) { true -> "answering"; false -> "silent"; null -> "not asked yet" })
    GaugeRow("Phone link", s.linked, when (s.linked) {
        true -> s.linkDetail.ifBlank { "linked" }; false -> s.linkDetail.ifBlank { "not linked" }; null -> "—" })
    GaugeRow("Phone PIN", !s.needsPhonePin, if (s.needsPhonePin) "type it once" else "saved")
    GaugeRow("Termux", s.termux, if (s.termux) "ready" else "not installed")
    IronButton("Tap the gauges", onRefresh, enabled = !s.busy)
}

@Composable
private fun GaugeRow(label: String, ok: Boolean?, text: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Lamp(ok)
        Text(label, color = Ink, fontFamily = Alegreya, fontSize = 15.sp, modifier = Modifier.weight(1f))
        Text(text, color = if (ok == false) Danger else Muted, fontFamily = Typewriter, fontSize = 12.sp, textAlign = TextAlign.End)
    }
}

@Composable
private fun TermuxPlate(s: UiState, a: Actions) = RivetPlate(title = "Levers") {
    if (!s.termux) {
        Hint("Termux opens the lab menu and the room on your computer (optional).")
        BrassButton("Install Termux", a.installTermux)
    } else {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            BrassButton("Lab menu", a.labMenu, Modifier.weight(1f), enabled = s.pairing.paired)
            BrassButton("Counting room", a.room, Modifier.weight(1f), enabled = s.pairing.paired)
        }
    }
}

@Composable
private fun PhonePinSetup(onSet: (String, String) -> Unit) {
    var pin by remember { mutableStateOf("") }
    var again by remember { mutableStateOf("") }
    RivetPlate(title = "Phone PIN, once", rim = Glow) {
        Hint("The PIN you chose for this phone in the Workshop. Kept encrypted here; asked after your fingerprint.")
        PinField(pin, { pin = it }, "Phone PIN")
        PinField(again, { again = it }, "Again")
        BrassButton("Save", { onSet(pin, again); pin = ""; again = "" })
    }
}

@Composable
private fun PinField(value: String, onChange: (String) -> Unit, label: String) {
    OutlinedTextField(value = value, onValueChange = { onChange(it.filter(Char::isDigit).take(12)) },
        label = { Text(label, fontFamily = Alegreya) }, singleLine = true, visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
        textStyle = TextStyle(fontFamily = Typewriter, fontSize = 20.sp, letterSpacing = 6.sp, color = Ink),
        modifier = Modifier.fillMaxWidth(),
        colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = Glow, unfocusedBorderColor = BrassDim, focusedLabelColor = Glow,
            unfocusedLabelColor = Muted, cursorColor = Glow, focusedTextColor = Ink, unfocusedTextColor = Ink,
            focusedContainerColor = Iron.copy(alpha = .5f), unfocusedContainerColor = Iron.copy(alpha = .5f)))
}
