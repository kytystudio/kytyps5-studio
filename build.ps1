# Builds dist\KytyPS5Studio\KytyPS5Studio.exe (needs: pip install pywebview pyinstaller)
$here = $PSScriptRoot
python -m PyInstaller --noconfirm --clean --windowed --name KytyPS5Studio --icon "$here\icon.ico" `
  --add-data "$here\ui;ui" --add-data "$here\icon.ico;." `
  --distpath "$here\dist" --workpath "$env:TEMP\kyty_build" --specpath "$env:TEMP\kyty_build" "$here\studio.py"
