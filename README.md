# KytyPS5 Studio

A console-style launcher and manager for [KytyPS5](https://github.com/KytyPS5/KytyPS5) builds on Windows.
Pick a game from a home screen inspired by the PlayStation 5, choose which emulator build runs it, keep
those builds up to date, and keep your saves and trophies safe across updates.

> Not affiliated with Sony Interactive Entertainment, PlayStation or the KytyPS5 project. See [LEGAL.md](LEGAL.md).
> Studio contains **no games, firmware, emulator builds or patches**; everything is downloaded by you.

## What it does
- **Games**: scans your library folders (folders with `sce_sys/param.json`) and shows the icon and background art.
- **Forks**: installs and updates builds from their GitHub releases (Windows zip). Several can live side by side;
  each game can use its own. Only the upstream KytyPS5 is listed on a fresh install.
- **Saves survive updates and are shared by every fork**: `_SaveData`, `_Trophies` and `_DownloadData` live in
  `shared-user-data` and are linked into each version folder; shader caches stay per fork in `forks/<fork>/_data`.
  Nothing is overwritten without a copy being kept in `userdata-conflicts`.
- **ZAR**: the Library settings list each game's format (Folder or ZAR) and can convert a game folder into a single
  `.zar` archive with the bundled ZArchive-Next tool, optionally removing the folder afterwards. Studio keeps the title,
  art and trophy data beside the archive and plays time per title, so conversion loses nothing.
- **Users**: PS5-style user selection. Saves, trophies and play time are kept per user (emulator `--user-id`).
- **Trophies**: reads the game's `trophy00.ucp`, shows levels, points and per-game lists, per user.
- **Shaders**: applies each fork's preset (`*-preset.json`) like its own launcher, keeps the shader cache between updates,
  and can run a fork's precompile tool.
- **Patches** (optional): game patches per game with per-option switches; version-checked against the game.
- **Controller first**: gamepad and keyboard navigation, on-screen keyboard, Control Center, in-game menu on
  PS button or Create + Options.

## Run from source
```bash
pip install -r requirements.txt
python studio.py            # native window (WebView2); add --browser for an Edge/Chrome app window
python studio.py --no-browser --port 8765   # only the server, for development
```
Needs Windows 10/11 with the WebView2 runtime (included with Windows 11) and Python 3.9+.

## Build the installer
```powershell
powershell -ExecutionPolicy Bypass -File build.ps1     # dist\KytyPS5Studio\KytyPS5Studio.exe (downloads ZArchive-Next 0.1 from its GitHub release, so it needs internet)
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss    # installer\KytyPS5Studio-Setup.exe
```
User data is stored in `%LOCALAPPDATA%\KytyPS5Studio` when installed, or in `data/` when run from source.

## Community forks and patches (optional, nothing included)
Studio ships with no forks or patches; only the upstream KytyPS5 build is listed. Its default **catalog address** is
`kytystudio/kytyps5-studio-catalog` ([the catalog repository](https://github.com/kytystudio/kytyps5-studio-catalog)):
when you open **Settings → Community (advanced)** Studio reads that metadata-only list (names, descriptions, links,
checksums). Nothing is downloaded or installed until you press Install. There you can also change or clear the
catalog address, import a catalog folder, open the editable community folder, import a patch file, or add a fork by GitHub URL.

## Logo
The cat-shaped gamepad logo (`ui/logo.svg`, `icon.ico`) is the KytyPS5 project's artwork. Ask its authors before
distributing Studio, or replace those two files with your own. Details in [LEGAL.md](LEGAL.md).

## License
No license is set yet; choose one before publishing. Third-party licenses are in
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).
