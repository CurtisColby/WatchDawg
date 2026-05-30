# Building & Installing WatchDawg TV

This is the **source project** for the WatchDawg Android TV client. You build it
once into an APK, then sideload that APK onto your TV.

---

## 1. Prerequisites (on a computer, not the TV)

You need the Android SDK. Easiest path is **Android Studio** (includes everything):

- Android Studio (Hedgehog or newer): https://developer.android.com/studio
- It will install: Android SDK, build-tools, and a JDK (17).

If you prefer command line only, install the **Android command-line tools** and
a **JDK 17**, then set `ANDROID_HOME` / `ANDROID_SDK_ROOT` to your SDK path.

This project targets:
- compileSdk / targetSdk **35**
- minSdk **21** (covers virtually all Android TV / Google TV / Fire TV devices)
- Kotlin **2.0.20**, AGP **8.5.2**, JDK **17**

---

## 2. Build the APK

### Option A — Android Studio (simplest)
1. **File ▸ Open** and select this project folder (the one with `settings.gradle.kts`).
2. Let Gradle sync (first sync downloads dependencies — needs internet).
3. **Build ▸ Build App Bundle(s) / APK(s) ▸ Build APK(s)**.
4. When it finishes, click **locate** in the notification. The file is:
   `app/build/outputs/apk/debug/app-debug.apk`

You can also just press **Run ▸ Run 'app'** with your TV connected via ADB and it
installs + launches directly.

### Option B — Command line
From the project root:

```bash
# Linux / macOS
./gradlew assembleDebug

# Windows
gradlew.bat assembleDebug
```

Output APK:
```
app/build/outputs/apk/debug/app-debug.apk
```

> First build downloads Gradle and all dependencies; give it a few minutes and
> keep internet on. Subsequent builds are fast.

---

## 3. Put the APK on your TV

The app is sideloaded (not from the Play Store), so the TV must allow
**Install from unknown sources / unknown apps** for whatever app does the install.

Pick whichever fits your setup:

### Path 1 — ADB over the network (cleanest for re-installs)
On the TV: enable **Developer options** (Settings ▸ Device ▸ About ▸ click
"Build" 7 times) and turn on **USB/Network debugging**. Then from your computer:

```bash
adb connect <TV_IP>:5555
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

`-r` reinstalls over an existing copy, keeping your server-address setting.

### Path 2 — File manager on the TV (the "browse to it and install" path)
1. Copy `app-debug.apk` somewhere the TV can reach:
   - a **USB stick**, or
   - a folder served by your existing WatchDawg/Plex server, or any LAN share.
2. On the TV, install a file manager / sideload helper if you don't have one:
   - **Downloader** (by AFTVnews) — great on Fire TV; can fetch the APK from a URL.
   - **X-plore** or **Solid Explorer** — browse USB/network and tap the APK.
3. Navigate to the APK on the TV, select it, and confirm install. Approve the
   "unknown sources" prompt for that file manager when asked.

### Path 3 — Fire TV specifically
Use **Downloader**: enter a direct URL to the APK (e.g. served by your server),
download, install. Enable *Apps from Unknown Sources* for Downloader first
(Settings ▸ My Fire TV ▸ Developer Options).

---

## 4. First launch

1. Open **WatchDawg** from the TV's app row (it uses the Leanback banner).
2. It drops straight into the **Feed**, talking to the default server
   `http://192.168.50.42:6868`.
3. To change the server: go to **Settings** (left rail) and edit the address.

### The hidden PIN lock
- From **any screen**, press the D-pad sequence **Up, Up, Down, Down** to open
  the PIN pad.
- Enter your backend PIN to unlock locked channels + the PIN-gated library.
- A small unlock glyph appears next to the WatchDawg logo while unlocked.
- Press **Lock Now** in the pad, or simply close the app, to re-lock. The token
  is held in memory only and never written to disk.

---

## 5. Controls cheat-sheet (in the player)

| Action | Control |
|---|---|
| Play / pause | OK (short press) |
| Favorite current | OK (long press) |
| Next / previous in queue | D-pad Right / Left (single) |
| Seek +10s / −10s | D-pad Right / Left (double-tap) |
| Skip (blocklist + advance) | D-pad Down |
| Exit (saves resume point) | Back |

> Skip is the only destructive action — it adds the video to the backend's
> global blocklist. Next / video-end / arrow navigation never blocklists.

---

## Troubleshooting

- **"App not installed" on the TV** → an older copy with a different signature
  is present; uninstall it first, or use `adb install -r`.
- **Feed can't reach server** → check the address in Settings, confirm the TV is
  on the same LAN, and that the backend is up on the given IP:port.
- **Cleartext/HTTP blocked** → already handled; the app permits cleartext to the
  LAN backend by design. If you move the backend to HTTPS later, tighten
  `res/xml/network_security_config.xml`.
- **Gradle sync fails on first run** → ensure JDK 17 is selected
  (Android Studio ▸ Settings ▸ Build Tools ▸ Gradle ▸ Gradle JDK = 17).
