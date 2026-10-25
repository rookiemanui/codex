# 小锅账本 Android companion

This is the installable Android client for the existing computer-hosted ledger. It connects to the Windows ledger over a private LAN address or an HTTPS endpoint; it does not store an offline copy of the database.

Requirements: JDK 17, Android SDK platform 36 and build tools 35.0.0, Gradle 8.13. Run `gradlew.bat assembleDebug testDebugUnitTest` in this directory. The generated debug APK is at `app/build/outputs/apk/debug/app-debug.apk`.

Only `INTERNET` permission is requested. HTTP is enabled for private IPv4 LAN addresses, while public endpoints require HTTPS. The native shell verifies `/api/status` before loading the ledger. WebView remains on the selected origin, and external links open in the user's browser. Android's file picker handles receipt uploads and save locations for backup/CSV exports. A native print adapter handles `window.print()`.
