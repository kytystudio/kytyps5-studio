param([string]$OutputDir = (Join-Path $PSScriptRoot "dist"))

# Builds $OutputDir\KytyPS5Studio\KytyPS5Studio.exe (needs: pip install -r requirements.txt)
$here = $PSScriptRoot
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not [IO.Path]::IsPathRooted($OutputDir)) { $OutputDir = Join-Path $here $OutputDir }

python -c "import zstandard" *> $null
if ($LASTEXITCODE -ne 0) {
  python -m pip install "zstandard==0.25.0"
  if ($LASTEXITCODE -ne 0) { throw "Could not install the pinned ZAR reader dependency (zstandard)." }
}

# Pin the archive tool version used by the released app. It is bundled into
# PyInstaller's _internal\tools directory and included by installer.iss.
$zarVersion = "0.1"
$zarCache = Join-Path $env:TEMP "kyty-studio-zarchive-$zarVersion"
$zarExe = Join-Path $zarCache "zarchive.exe"
if (-not (Test-Path -LiteralPath $zarExe -PathType Leaf)) {
  New-Item -ItemType Directory -Force -Path $zarCache | Out-Null
  $releaseUrl = "https://api.github.com/repos/Mario-119/ZArchive-Next/releases/tags/$zarVersion"
  $release = Invoke-RestMethod -Uri $releaseUrl -Headers @{ "User-Agent" = "KytyPS5Studio-Build"; Accept = "application/vnd.github+json" }
  $asset = $release.assets | Where-Object { $_.name -match '(?i)(windows|win|x64|amd64)' -and $_.name -match '(?i)\.(zip|exe)$' } | Select-Object -First 1
  if (-not $asset) { $asset = $release.assets | Where-Object { $_.name -match '(?i)^zarchive\.exe$' } | Select-Object -First 1 }
  if (-not $asset) { throw "ZArchive-Next $zarVersion has no recognizable Windows executable asset." }
  $assetUri = [Uri]$asset.browser_download_url
  if ($assetUri.Scheme -ne "https" -or $assetUri.Host -ne "github.com") { throw "Unexpected ZArchive download host: $($assetUri.Host)" }
  $download = Join-Path $zarCache $asset.name
  Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $download -Headers @{ "User-Agent" = "KytyPS5Studio-Build" }
  if ($asset.digest -match '^sha256:([0-9a-fA-F]{64})$') {
    $actualDigest = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash
    if ($actualDigest -ne $Matches[1]) { Remove-Item -LiteralPath $download -Force; throw "ZArchive release SHA-256 did not match GitHub's published digest." }
  }
  if ([IO.Path]::GetExtension($download) -ieq ".zip") {
    $unpack = Join-Path $zarCache "unpacked"
    if (Test-Path -LiteralPath $unpack) { Remove-Item -LiteralPath $unpack -Recurse -Force }
    Expand-Archive -LiteralPath $download -DestinationPath $unpack -Force
    $found = Get-ChildItem -LiteralPath $unpack -Filter "zarchive.exe" -File -Recurse | Select-Object -First 1
    if (-not $found) { throw "The ZArchive release archive did not contain zarchive.exe." }
    Copy-Item -LiteralPath $found.FullName -Destination $zarExe -Force
  } else {
    if ([IO.Path]::GetFullPath($download) -ne [IO.Path]::GetFullPath($zarExe)) {
      Copy-Item -LiteralPath $download -Destination $zarExe -Force
    }
  }
}

# Packwright extracts downloaded game packages (used by the Downloads plugin through studio.tool()).
# Its self-contained Windows runtime is installed into an isolated staging folder,
# copied into the PyInstaller bundle as tools\packwright, then uninstalled again.
$packwrightVersion = "1.1.0"
$packwrightCache = Join-Path $env:TEMP "kyty-studio-packwright-$packwrightVersion"
$packwrightDir = Join-Path $packwrightCache "runtime"
$packwrightExe = Join-Path $packwrightDir "Packwright.exe"
$packwrightSource = Join-Path $packwrightCache "Packwright-source-v$packwrightVersion.zip"
$packwrightInstallerName = "Packwright-Setup-v$packwrightVersion-windows-x64.exe"
$packwrightInstaller = Join-Path $packwrightCache $packwrightInstallerName
$packwrightInstallerSha256 = "8368CEE69079DC32A6E66767EC940716319383D867E1EB67CC6F40000685F4E4"
if (-not (Test-Path -LiteralPath $packwrightExe -PathType Leaf)) {
  New-Item -ItemType Directory -Force -Path $packwrightCache | Out-Null
  if (-not (Test-Path -LiteralPath $packwrightInstaller -PathType Leaf)) {
    $url = "https://github.com/itsmemac/Packwright/releases/download/v$packwrightVersion/$packwrightInstallerName"
    Invoke-WebRequest -Uri $url -OutFile $packwrightInstaller -Headers @{ "User-Agent" = "KytyPS5Studio-Build" }
  }
  $installerHash = (Get-FileHash -LiteralPath $packwrightInstaller -Algorithm SHA256).Hash
  if ($installerHash -ne $packwrightInstallerSha256) {
    Remove-Item -LiteralPath $packwrightInstaller -Force
    throw "Packwright installer SHA-256 did not match the pinned release digest."
  }
  $staging = Join-Path $packwrightCache "staging"
  if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
  $install = Start-Process -FilePath $packwrightInstaller -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS", "/DIR=`"$staging`""
  ) -Wait -PassThru -WindowStyle Hidden
  if ($install.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $staging "Packwright.exe"))) {
    throw "Packwright $packwrightVersion could not be staged (installer exit $($install.ExitCode))."
  }
  New-Item -ItemType Directory -Force -Path $packwrightDir | Out-Null
  Get-ChildItem -LiteralPath $staging -Force | Where-Object { $_.Name -notmatch '^unins\d*\.' } |
    Copy-Item -Destination $packwrightDir -Recurse -Force
  $uninstaller = Get-ChildItem -LiteralPath $staging -Filter "unins*.exe" -File | Select-Object -First 1
  if ($uninstaller) { Start-Process -FilePath $uninstaller.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES" -Wait -WindowStyle Hidden }
  if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
# Packwright is GPL-3: ship its source archive next to the binaries.
if (-not (Test-Path -LiteralPath $packwrightSource -PathType Leaf)) {
  $sourceUrl = "https://codeload.github.com/itsmemac/Packwright/zip/refs/tags/v$packwrightVersion"
  Invoke-WebRequest -Uri $sourceUrl -OutFile $packwrightSource -Headers @{ "User-Agent" = "KytyPS5Studio-Build" }
}

python -m PyInstaller --noconfirm --clean --windowed --name KytyPS5Studio --icon "$here\icon.ico" `
  --add-data "$here\ui;ui" --add-data "$here\icon.ico;." --add-data "$packwrightDir;tools\packwright" `
  --add-data "$packwrightSource;." --add-data "$here\licenses\zstandard-LICENSE.txt;licenses" --add-binary "$zarExe;tools" `
  --distpath $OutputDir --workpath "$env:TEMP\kyty_build" --specpath "$env:TEMP\kyty_build" "$here\studio.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
$appDir = Join-Path $OutputDir "KytyPS5Studio"
Copy-Item -LiteralPath (Join-Path $here "LEGAL.md") -Destination $appDir -Force
Copy-Item -LiteralPath (Join-Path $here "THIRD_PARTY_LICENSES.txt") -Destination $appDir -Force
