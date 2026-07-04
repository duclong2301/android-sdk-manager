# Android SDK Manager

A lightweight, standalone Android SDK package manager for developers who want the Android SDK without installing Android Studio.

The app provides a local web interface for downloading Google's Android command-line tools, browsing SDK packages, installing or uninstalling packages, accepting SDK licenses, and viewing the environment variables needed by Android build tools.

## Features

- Install Android command-line tools directly from Google's SDK repository.
- Browse the Android SDK catalog with package type filtering and search.
- Install common packages such as `platform-tools`, `cmdline-tools;latest`, `platforms;android-*`, `build-tools;*`, and `emulator`.
- Accept SDK licenses through `sdkmanager`.
- Refresh and display installed packages.
- Generate platform-specific `ANDROID_SDK_ROOT`, `ANDROID_HOME`, and `PATH` setup commands.
- Runs locally in a browser with no external Python dependencies.

## Requirements

- Python 3.9 or newer.
- Java/JDK for Google's `sdkmanager`.
- Internet access when downloading command-line tools or SDK packages.

Android Studio is not required.

## Quick Start

macOS:

```bash
./run.command
```

Windows:

```bat
run.bat
```

Linux or manual launch:

```bash
python3 app.py
```

The app opens at:

```text
http://127.0.0.1:8765/
```

## Recommended Workflow

1. Choose an SDK folder or keep the default path.
2. Click `Install command-line tools`.
3. Click `Load catalog`.
4. Select the packages you need.
5. Click `Install packages`.
6. Copy the environment variable commands shown in the `Environment` panel.

For a typical Android development setup, install:

- `platform-tools`
- `cmdline-tools;latest`
- The latest `platforms;android-*`
- The latest `build-tools;*`
- `emulator` if you need Android virtual devices

## Notes

This project wraps Google's official Android SDK command-line tooling. Package installation, uninstallation, and license acceptance are handled by `sdkmanager`.

If package installation fails because Java is missing, install a JDK first and restart the app.

Runtime cache files such as `config.json`, `catalog-cache.json`, and `installed-cache.json` are intentionally ignored by Git.
