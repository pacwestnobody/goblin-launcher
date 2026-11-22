package com.goblin.launcher.termux

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri

/**
 * Detect / open / offer install for Termux (+ Termux:API) used for lab shell
 * and Android-style dialog prompts from scripts.
 *
 * We cannot silently install APKs without user action — open F-Droid / Play.
 */
object TermuxHelper {
    const val TERMUX_PKG = "com.termux"
    const val TERMUX_API_PKG = "com.termux.api"

    /** F-Droid package pages (preferred for Termux). */
    private const val FDROID_TERMUX = "https://f-droid.org/packages/com.termux/"
    private const val FDROID_TERMUX_API = "https://f-droid.org/packages/com.termux.api/"
    private const val PLAY_TERMUX = "market://details?id=com.termux"
    private const val PLAY_TERMUX_API = "market://details?id=com.termux.api"
    private const val PLAY_WEB_TERMUX = "https://play.google.com/store/apps/details?id=com.termux"

    data class Status(
        val termuxInstalled: Boolean,
        val termuxApiInstalled: Boolean,
    ) {
        val ready: Boolean get() = termuxInstalled
        val fullyReady: Boolean get() = termuxInstalled && termuxApiInstalled
        val label: String
            get() = when {
                fullyReady -> "Termux + API installed"
                termuxInstalled -> "Termux OK — install Termux:API for dialogs"
                else -> "Termux not installed"
            }
    }

    fun status(context: Context): Status {
        val pm = context.packageManager
        return Status(
            termuxInstalled = isInstalled(pm, TERMUX_PKG),
            termuxApiInstalled = isInstalled(pm, TERMUX_API_PKG),
        )
    }

    private fun isInstalled(pm: PackageManager, pkg: String): Boolean {
        return try {
            pm.getPackageInfo(pkg, 0)
            true
        } catch (_: PackageManager.NameNotFoundException) {
            false
        }
    }

    /** Open Termux app if installed. */
    fun openTermux(context: Context): Boolean {
        val pm = context.packageManager
        val launch = pm.getLaunchIntentForPackage(TERMUX_PKG) ?: return false
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            context.startActivity(launch)
            true
        } catch (_: ActivityNotFoundException) {
            false
        }
    }

    /**
     * Try to run a command in Termux via RUN_COMMAND (needs Termux:API +
     * user-granted permission in Termux settings on recent versions).
     */
    fun runInTermux(context: Context, command: String, background: Boolean = false): Boolean {
        if (!status(context).termuxInstalled) return false
        val intent = Intent().apply {
            setClassName(TERMUX_PKG, "com.termux.app.RunCommandService")
            action = "com.termux.RUN_COMMAND"
            putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash")
            putExtra(
                "com.termux.RUN_COMMAND_ARGUMENTS",
                arrayOf("-lc", command),
            )
            putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home")
            putExtra("com.termux.RUN_COMMAND_BACKGROUND", background)
            putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", "0")
        }
        return try {
            context.startService(intent)
            true
        } catch (_: Exception) {
            // Fall back to just opening Termux
            openTermux(context)
        }
    }

    fun openInstallTermux(context: Context) {
        openUrlOrMarket(context, FDROID_TERMUX, PLAY_TERMUX, PLAY_WEB_TERMUX)
    }

    fun openInstallTermuxApi(context: Context) {
        openUrlOrMarket(
            context,
            FDROID_TERMUX_API,
            PLAY_TERMUX_API,
            "https://play.google.com/store/apps/details?id=com.termux.api",
        )
    }

    /** Ensure Termux is present: open install if missing, else true. */
    fun ensureTermux(context: Context): Boolean {
        val st = status(context)
        if (st.termuxInstalled) return true
        openInstallTermux(context)
        return false
    }

    fun ensureTermuxApi(context: Context): Boolean {
        val st = status(context)
        if (!st.termuxInstalled) {
            openInstallTermux(context)
            return false
        }
        if (st.termuxApiInstalled) return true
        openInstallTermuxApi(context)
        return false
    }

    private fun openUrlOrMarket(
        context: Context,
        fDroid: String,
        market: String,
        webPlay: String,
    ) {
        // Prefer F-Droid page (official Termux distribution)
        val attempts = listOf(
            Intent(Intent.ACTION_VIEW, Uri.parse(fDroid)),
            Intent(Intent.ACTION_VIEW, Uri.parse(market)),
            Intent(Intent.ACTION_VIEW, Uri.parse(webPlay)),
        )
        for (intent in attempts) {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            try {
                if (intent.resolveActivity(context.packageManager) != null) {
                    context.startActivity(intent)
                    return
                }
            } catch (_: ActivityNotFoundException) {
                continue
            }
        }
        // Last resort: browser any https
        try {
            context.startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse(fDroid)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        } catch (_: Exception) {
            // ignore
        }
    }
}
