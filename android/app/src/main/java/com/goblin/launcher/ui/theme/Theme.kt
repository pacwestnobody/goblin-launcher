package com.goblin.launcher.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.goblin.launcher.R

// Goblin Launcher palette: same tokens as the desktop page (ui_static/index.html :root),
// pulled from the owner's wallpaper.
val Iron = Color(0xFF050606)
val Plate = Color(0xFF131718)
val PlateHi = Color(0xFF22282A)
val Brass = Color(0xFFB08A52)
val BrassDim = Color(0xFF6E5434)
val Copper = Color(0xFFB0643A)
val Patina = Color(0xFF6F8A5E)
val Glow = Color(0xFFF29A18)
val Paper = Color(0xFFD9CCB0)
val Ink = Color(0xFFCFC6B4)
val Muted = Color(0xFF877968)
val Danger = Color(0xFFE0341C)
val Ember = Color(0xFF2A1206)

// Older names the rest of the app still uses.
val VaultBg = Iron
val BrassSoft = Paper
val TextPrimary = Ink
val TextMuted = Muted
val OkGreen = Patina
val BadRed = Danger
val Surface = Plate
val Border = BrassDim

val Fell = FontFamily(Font(R.font.fell_sc))
val Alegreya = FontFamily(Font(R.font.alegreya), Font(R.font.alegreya_bold, FontWeight.Bold))
val Typewriter = FontFamily(Font(R.font.special_elite))

private val GoblinColors = darkColorScheme(
    primary = Brass,
    onPrimary = Iron,
    secondary = Glow,
    background = Iron,
    onBackground = Ink,
    surface = Plate,
    onSurface = Ink,
    surfaceVariant = PlateHi,
    onSurfaceVariant = Muted,
    outline = BrassDim,
    error = Danger,
    onError = Ink,
)

private val GoblinType = Typography(
    bodyLarge = TextStyle(fontFamily = Alegreya, fontSize = 16.sp),
    bodyMedium = TextStyle(fontFamily = Alegreya, fontSize = 15.sp),
    bodySmall = TextStyle(fontFamily = Alegreya, fontSize = 13.sp),
    labelLarge = TextStyle(fontFamily = Fell, fontSize = 16.sp),
    labelMedium = TextStyle(fontFamily = Alegreya, fontSize = 13.sp),
    titleLarge = TextStyle(fontFamily = Fell, fontSize = 22.sp),
    titleMedium = TextStyle(fontFamily = Fell, fontSize = 18.sp),
)

@Composable
fun GoblinTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = GoblinColors, typography = GoblinType, content = content)
}
