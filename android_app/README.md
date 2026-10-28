# CurrencyAI - Native Android Application

This folder contains the complete, production-ready Android Studio project for **AI-Powered Smart Currency Detection System**.

---

## Project Structure
```text
android_app/
├── app/
│   ├── build.gradle                          # App dependencies & SDK configuration (SDK 34)
│   ├── src/main/
│   │   ├── AndroidManifest.xml               # Camera, Audio, & Cleartext permissions
│   │   ├── java/com/salman/currencyai/
│   │   │   └── MainActivity.java             # WebView controller with auto-granted camera permissions
│   │   └── res/
│   │       ├── layout/activity_main.xml      # Fullscreen UI container
│   │       ├── values/                       # App strings, themes, and dark palette
│   │       └── mipmap-*/                     # HD CurrencyAI launcher icons
├── build.gradle                              # Top-level Gradle configuration
├── gradle.properties                         # AndroidX & memory flags
└── settings.gradle                           # Module definitions
```

---

## How to Build the APK in Android Studio

1. Open **Android Studio** on any computer.
2. Select **File** → **Open...**
3. Select this folder:
   `C:\Users\Salman Ballari\Downloads\AI_Currency_Detection(updated) (1)\AI_Currency_Detection_System(12)\android_app`
4. Wait for Gradle sync to complete.
5. Click **Build** → **Build Bundle(s) / APK(s)** → **Build APK(s)**.
6. Android Studio will generate:
   `android_app/app/build/outputs/apk/debug/app-debug.apk`
7. Copy `app-debug.apk` to your Android phone and tap it to install!

---

## How It Operates Without Cloud / 3rd Party Websites

1. **Option 1 (Standalone on Phone):** Run Python on the phone using **Pydroid 3** or **Termux**. The phone runs the backend internally on `http://127.0.0.1:5000` with 0 laptop or internet needed.
2. **Option 2 (Direct Phone-Laptop Hotspot):** Connect phone to your computer's Wi-Fi / Hotspot. The app communicates directly over your local Wi-Fi at `http://192.168.43.196:5000` with 0 data uploaded to any 3rd party website.
