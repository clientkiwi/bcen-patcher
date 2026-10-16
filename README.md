# Battle Cats EN Patcher

Play the **Japanese version of The Battle Cats (にゃんこ大戦争)** in English on an Android emulator (LDPlayer 9), with JP's newer content and the **official English text and images** from the English version.

The patcher takes the English game's own files and puts them into a copy of the Japanese game. The copy is installed as a separate app called **"Battle Cats"**, next to your original. A small window then keeps it updated whenever the Japanese game updates.

> ### ⚠ Disclaimer
> - This is an **unofficial fan tool**. It is **not affiliated with or endorsed by PONOS**.
> - It **modifies the game**, which is against PONOS's terms of use. **Your account could be banned.** Use it at your own risk.
> - **No game files are included or distributed.** The patcher works only with copies of both games that **you** installed. Don't share the apps it builds: they contain PONOS's game.

## What you get

- **Official English text:** about 35,000 strings, matched by ID. That covers unit and enemy names and descriptions, stage and map names, menus, dialogs, missions, combos, Gamatoto and more.
- **English images:** about 6,600 image sets with text in them, like buttons, labels, stage titles and many banners.
- **Japan-only content stays Japanese.** Anything the English game doesn't have yet keeps its Japanese text until the English game catches up; the next update then picks up the official English automatically. You can fill gaps yourself in `custom_names.csv`.
- **Your original Japanese game is never changed.**

## Requirements

- **Windows 10 or 11.**
- **[LDPlayer 9](https://www.ldplayer.net/)** with these settings (**Settings → Other settings**):
  - **ADB debugging → Open local connection** (required)
  - **Root permission → On** (recommended; see below)
- In LDPlayer, both games installed and opened once, so they download their data:
  - **にゃんこ大戦争 (Japanese)**, e.g. from **QooApp** if the Japanese Play Store isn't available to you
  - **The Battle Cats (English)**, from the Play Store
- **Python 3.12** and a **Java JDK** (17 or newer). `setup.bat` offers to install both.

**Why root is recommended:**
- **Event images:** with root, the patcher can also swap images the game downloads for events. Without it, those stay Japanese.
- **Save backups:** with root, the English app's save is backed up before every update. Without root, updates still work, but without backups.

## Setup

1. Download this project and unzip it anywhere.
2. Double-click **`setup.bat`**. It checks for or installs Python and Java, and creates the patcher's own Python environment in `%USERPROFILE%\.bcen-patcher`.
3. Set up LDPlayer as described under Requirements.
4. Double-click **`run.bat`**.

## Using it

The patcher window shows the versions of the Japanese game, the English game and your English copy.

- **Update English app:** builds the English copy and installs it. The first time takes 5–15 minutes, mostly copying and repacking about 1 GB of game data. Later updates skip anything that hasn't changed.
- **Check now:** looks for changes. It also checks automatically every 30 minutes while running and shows a notification when an update is needed.
- **Roll back:** reinstalls the previous build if something's wrong with the new one.
- **Closing the window** keeps it running in the tray (yellow **EN** icon). Right-click the icon → Quit to exit.

**When the Japanese game updates:**
1. Update にゃんこ大戦争 (QooApp, Play Store, wherever you got it).
2. Open it once so it downloads the new data.
3. In the patcher, click **Update English app**.

Your English copy shows an "update required" message until you do; ignore its store link.

**Moving your account:** use the game's own **Data Transfer** (引き継ぎ) to move your progress from the original app into the English copy, or start fresh.

## Checking a downloaded game file is genuine

If QooApp lags behind and you get the Japanese game from a download site, check the file before installing it:

```bash
%USERPROFILE%\.bcen-patcher\venv\Scripts\python -m bcen_patcher.apksig path\to\download.xapk
```

It verifies the signature of every part of the app and prints the signer's fingerprint. Genuine PONOS releases of にゃんこ大戦争 and The Battle Cats are signed by:

`baf876d554213331c6fe5f6bbf9ae9af2f95c20e82b14bc232b0ac3a77680cb1`

- **Pass:** every line says `ok` and the fingerprint matches. The file is PONOS's original, unchanged, wherever it came from.
- **Fail:** anything else (a failed check, a different fingerprint, or a file that turns out to be a "download manager" app) means don't install it.
- **Keep the current version installed while updating.** Android itself refuses an update that isn't signed by the same developer, so a fake gets refused automatically.

## Safety checks

Nothing is installed unless all of these pass:
- **Pack round-trip:** every rebuilt game data pack decrypts back to exactly the files that went in.
- **Checksums:** all of the game's internal pack checksums were found and updated.
- **Alignment:** about 275 known Japanese→English name pairs still line up in the merged files. This catches the two games' files falling out of step after an update.
- **Install:** the install succeeds; otherwise your current version stays.

## Your signing key: keep it safe

The first build creates a personal **signing key**. Every update must be signed with the same key, or Android refuses to update the app, and reinstalling would delete its save.
- **Where it is:** `%USERPROFILE%\.bcen-patcher\work\nyanko.keystore`
- **Backup:** a copy is kept in `signing_key_backup\` in the patcher folder. Keep that backup, and **never share or upload it**.

## Custom names (`custom_names.csv`)

English for Japan-only text the English game doesn't have yet:

```csv
japanese,english,note
例のヤツ,Those Guys,enemy name
```

- **Matching:** the Japanese must match the game's text exactly (spaces and punctuation are ignored).
- **Priority:** official English always wins; these entries only fill gaps.
- **When it applies:** on the next **Update English app**.

## Settings (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `app_name` | `Battle Cats` | Name of the English copy in the emulator (max 21 bytes) |
| `check_minutes` | `30` | How often to check for updates (0 = never) |
| `work_dir` | *(empty)* | Where game data and builds go (default `%USERPROFILE%\.bcen-patcher\work`, about 2–3 GB) |
| `adb` | *(empty)* | Path to the emulator's `adb.exe` (default: LDPlayer's, auto-detected) |
| `adb_serial` | *(empty)* | Which device to use if several are connected (see `adb devices`) |
| `java_bin` | *(empty)* | A JDK's `bin` folder (default: auto-detected) |
| `signature_v2` | `auto` | Adds the newer signature format, needed on Android 11+. `auto` = only when the device needs it |

## Troubleshooting

- **"no emulator found":** start LDPlayer and turn on ADB debugging (local connection). If you run several emulators, set `adb_serial`.
- **"…is not installed in the emulator":** install both games in LDPlayer and open each once.
- **"quality check failed":** after a game update, the Japanese and English files no longer line up as expected. Nothing was installed and your current version still works. Wait for an English update, or report it.
- **"install failed":**
  - **Signature mismatch:** if you lost your signing key, uninstall "Battle Cats" in LDPlayer first. Its save is lost unless you used Data Transfer.
  - **Anything else:** copy the message from the log and report it.
- **Some text is still Japanese:** that content isn't in the English game yet. It's replaced automatically once it is, or add it to `custom_names.csv`.

## How it works

1. **Copy:** copies both games (and, with root, their downloaded data) out of the emulator with `adb`.
2. **Decrypt:** decrypts the game's data packs with [tbcml](https://codeberg.org/fieryhenry/tbcml).
3. **Text:** puts the English text into the Japanese text files, matched by ID or row, keeping the Japanese file layout.
4. **Images:** swaps Japanese text images for their English versions, only when the piece layout matches, and skips the English game's placeholder images.
5. **Repack:** re-encrypts the packs and updates their checksums in the game's native library.
6. **Rename:** gives the copy its own package name (`jp.co.ponos.battlecatz`), so it installs next to the original.
7. **Sign and install:** signs it with your own key and installs it with `adb install-multiple`.

Code: `bcen_patcher\` (`pipeline.py` runs the steps; `gui.py` is the window). Command line: `%USERPROFILE%\.bcen-patcher\venv\Scripts\python -m bcen_patcher.pipeline check|update|rollback`.

## Credits

- [tbcml](https://codeberg.org/fieryhenry/tbcml) by fieryhenry, for reading and writing the game's data packs.
- The Battle Cats / にゃんこ大戦争 © PONOS Corporation. This project is not affiliated with PONOS.
