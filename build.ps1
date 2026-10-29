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

python -m PyInstaller --noconfirm --clean --windowed --name KytyPS5Studio --icon "$here\icon.ico" `
  --add-data "$here\ui;ui" --add-data "$here\icon.ico;." `
  --add-data "$here\licenses\zstandard-LICENSE.txt;licenses" --add-binary "$zarExe;tools" `
  --distpath $OutputDir --workpath "$env:TEMP\kyty_build" --specpath "$env:TEMP\kyty_build" "$here\studio.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
$appDir = Join-Path $OutputDir "KytyPS5Studio"
Copy-Item -LiteralPath (Join-Path $here "LEGAL.md") -Destination $appDir -Force
Copy-Item -LiteralPath (Join-Path $here "THIRD_PARTY_LICENSES.txt") -Destination $appDir -Force
