# CodMod

An open-source mod manager for Call of Duty games that support mods. Pure Python + tkinter, no dependencies.

## Features
- Game folders with Steam-aware auto-detect and a **Launch** button (custom exe, AlterWare/Plutonium, or Steam)
- Mod loader downloads (AlterWare, Plutonium updater) and links (CoD4x, H1-Mod, S1-Mod, CoD2x)
- Mods from a catalog, a URL, a local file, **GitHub** search and **GameBanana** (per-game)
- **.zip, .7z and .rar** support (7z/rar use Windows 10/11's built-in `tar`)
- **Install preview** with smart destination (mods/zone/main detection) and **conflict warnings**
- **Backups** of overwritten files; clean uninstall restores originals
- **Enable / disable** mods without deleting them, plus **profiles** (e.g. "Zombies", "Vanilla")
- **Update checker** for GitHub and GameBanana installs
- **Downloads tab** with pause, cancel and retry
- **Mod packs**: export/import your setup as a `.codmodpack` file
- **Community catalog**: point Settings at a JSON file with `games` and/or `mods`
- BO3 Steam Workshop shortcut, light/dark theme, accent colors, optional self-update check

## Download
Grab `CodMod.exe` from the **Releases** page. It's a portable single file.

## Run from source
    python codmod.py

## Build the exe
Run `build.bat` on Windows, or push a tag (`git tag v3.0 && git push --tags`) and the GitHub Action
in `.github/workflows/build.yml` builds `CodMod.exe` and attaches it to a release.
For self-update, enter your `owner/name` repo in Settings.

## Add games, loaders or mods
Everything is data in `catalog.json` (or a community catalog URL). Pull requests welcome.
Only link to legitimate, legal mods and loaders - no cheats, cracks or pirated content.

## Notes
- No API keys are bundled. GitHub search works without one (optional token in Settings).
- Mods and packs run no code, but they replace game files. Only install from sources you trust.
- You must own the games. Don't use mods on official matchmaking servers.

Licensed under MIT.
