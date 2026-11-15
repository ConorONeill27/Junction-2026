package com.worldgit.hud.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

object WorldGitPalette {
    val Bg = Color(0xFF05080F)
    val Surface = Color(0xFF0B1220)
    val Card = Color(0xFF0F172A)
    val CardElevated = Color(0xFF152038)
    val Border = Color(0xFF1E293B)
    val BorderBright = Color(0xFF334155)

    val Cyan = Color(0xFF2EE8FF)
    val Cobalt = Color(0xFF64A5FF)
    val Emerald = Color(0xFF34D399)
    val Amber = Color(0xFFFBBF24)
    val Rose = Color(0xFFFB7185)

    val TextPrimary = Color(0xFFF8FAFC)
    val TextSecondary = Color(0xFF94A3B8)
    val TextMuted = Color(0xFF64748B)
}

private val WorldGitColorScheme = darkColorScheme(
    primary = WorldGitPalette.Cyan,
    onPrimary = WorldGitPalette.Bg,
    secondary = WorldGitPalette.Cobalt,
    onSecondary = WorldGitPalette.Bg,
    tertiary = WorldGitPalette.Emerald,
    onTertiary = WorldGitPalette.Bg,
    background = WorldGitPalette.Bg,
    onBackground = WorldGitPalette.TextPrimary,
    surface = WorldGitPalette.Surface,
    onSurface = WorldGitPalette.TextPrimary,
    surfaceVariant = WorldGitPalette.Card,
    onSurfaceVariant = WorldGitPalette.TextSecondary,
    outline = WorldGitPalette.Border,
    error = WorldGitPalette.Rose,
)

private val WorldGitTypography = Typography(
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 20.sp,
        letterSpacing = (-0.3).sp,
        color = WorldGitPalette.TextPrimary,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 15.sp,
        color = WorldGitPalette.TextPrimary,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp,
        color = WorldGitPalette.TextPrimary,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 13.sp,
        color = WorldGitPalette.TextPrimary,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.Medium,
        fontSize = 11.sp,
        letterSpacing = 0.6.sp,
        color = WorldGitPalette.TextSecondary,
    ),
)

@Composable
fun WorldGitTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = WorldGitColorScheme,
        typography = WorldGitTypography,
        content = content,
    )
}
