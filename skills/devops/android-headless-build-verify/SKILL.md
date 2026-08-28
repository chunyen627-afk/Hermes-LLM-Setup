---
name: android-headless-build-verify
description: Use for headless Android builds and emulator verification.
---

# Android Headless Build & Verify (Windows)

Build an Android app and prove it runs — all via CLI (git-bash), no Android Studio.
Proven on Windows 11 + Hermes desktop.

## 1. Install toolchain from zero (all under `C:\Users\<user>\dev`)

```bash
mkdir -p ~/dev/downloads && cd ~/dev/downloads
# JDK 17 (Temurin)
curl -sSL --retry 3 -o jdk17.zip "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse"
# Android cmdline-tools
curl -sSL -o cmdline-tools.zip "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
# Gradle
curl -sSL -o gradle-8.7-bin.zip "https://services.gradle.org/distributions/gradle-8.7-bin.zip"
```

Unzip; **JDK zip has an extra top dir** → `mv jdk17/jdk-17*/* jdk17/`.
cmdline-tools must land at `android-sdk/cmdline-tools/latest/bin/sdkmanager.bat` (rename the inner dir to `latest`).

## 2. Env script (git-bash)

`~/dev/android-env.sh`:
```bash
export JAVA_HOME='C:\Users\<user>\dev\jdk17'        # MUST be Windows-style path
export ANDROID_HOME='C:\Users\<user>\dev\android-sdk'
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
```
Usage: `source ~/dev/android-env.sh && gradle ...`

Pitfalls:
- `sdkmanager.bat`/`avdmanager.bat` need `JAVA_HOME` in **Windows format** (`C:\...`); MSYS `/c/...` fails.
- Native Windows tools (gradle, adb) don't accept MSYS paths — use `C:/Users/...`.
- Project `local.properties`: `sdk.dir=C\:\\Users\\<user>\\dev\\android-sdk`.

## 3. SDK packages + AVD

```bash
yes | sdkmanager.bat --licenses
sdkmanager.bat "platform-tools" "build-tools;34.0.0" "platforms;android-22" \
  "emulator" "system-images;android-22;google_apis;x86_64"
avdmanager.bat create avd -n myavd -k "system-images;android-22;google_apis;x86_64" -d pixel --force
```

Boot headless (background process):
```bash
emulator -avd myavd -no-window -gpu swiftshader_indirect -no-audio -no-boot-anim &
adb wait-for-device
adb shell 'while [[ -z $(getprop sys.boot_completed) ]]; do sleep 1; done'
```

## 4. Build, install, launch

```bash
gradle test assembleDebug --console=plain
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n <pkg>/<Activity>
```

## 5. Screenshots & input (verification)

```bash
adb exec-out screencap -p > shots/x.png      # PNG direct, faster than pull
adb shell input tap 540 960                  # tap
adb shell input swipe x y x y 3000           # long-press 3s (blocks 3s!)
```

**Long-press + screenshot**: run the blocking `input swipe` as a background
process, `sleep` to mid-hold, then screencap. Wrap in a `.sh` script — the
terminal tool rejects inline `&`.

## 6. Verify without burning vision rate limit

1. **PIL pixel analysis** (batch): find the playfield rect by its background
   color; locate white ball blob / colored UI elements; compute normalized
   coords to decide "ball in zone X" across 40 frames. No LLM needed.
2. **vision_analyze** only the 3–5 final frames, with specific questions
   (where's the ball? read SCORE? any glitches?). The vision API rate-limits
   hard (HTTP 429) — space calls ~60–150s apart, 1–2 images per call.

## 7. Game-physics tuning loop (if the app has physics)

Mirror the engine in a **pure-Python sim** (copy substep/collision code),
sweep parameters offline (launch velocity, wall heights, angles), THEN port
winning values to Kotlin. Cuts build→install→screenshot guess cycles 10x.
Keep the sim scripts in `docs/` as evidence.

## Acceptance checklist
- [ ] `gradle test` all green (count tests from XML: `app/build/test-results/**`)
- [ ] `gradle assembleDebug` zero warnings; record APK path+size
- [ ] Emulator booted to target API level (`adb shell getprop ro.build.version.sdk`)
- [ ] 4+ screenshots: start / in-play / action (e.g. flipper) / end-state
- [ ] Each screenshot vision-verified with a specific question
- [ ] Docs: setup record + architecture + shots notes
