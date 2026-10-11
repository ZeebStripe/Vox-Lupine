# Build the Windows installer: dist\VoxLupine-Setup-<version>.exe
#
#   powershell -ExecutionPolicy Bypass -File tools\build_installer.ps1
#
# Needs Python 3.11+ and Inno Setup 6 (winget install JRSoftware.InnoSetup).
# Uses its own environment in .build-venv, separate from the app's .venv.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Find-Python {
    foreach ($c in @("py -3", "python")) {
        $exe = $c.Split(" ")[0]
        if (Get-Command $exe -ErrorAction SilentlyContinue) { return $c }
    }
    throw "Python 3.11+ not found."
}

function Find-ISCC {
    $paths = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
               "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
               "$env:ProgramFiles\Inno Setup 6\ISCC.exe")
    foreach ($p in $paths) { if (Test-Path $p) { return $p } }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup"
}

$venvPy = Join-Path $root ".build-venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating .build-venv ..."
    $py = Find-Python
    Invoke-Expression "$py -m venv .build-venv"
}
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements-build.txt
if ($LASTEXITCODE) { throw "pip install failed" }

$version = & $venvPy -c "from about import VERSION; print(VERSION)"
Write-Host "Building Vox Lupine $version"

& $venvPy tools\make_icon.py
if ($LASTEXITCODE) { throw "icon generation failed" }

# Absolute paths: with --specpath, PyInstaller resolves relative paths from build\.
$ico = Join-Path $root "assets\vox-lupine.ico"
& $venvPy -m PyInstaller --noconfirm --clean --windowed `
    --name "Vox Lupine" `
    --icon $ico `
    --add-data "$ico;assets" `
    --distpath build\dist --workpath build\work --specpath build `
    app.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = Find-ISCC
& $iscc /Qp "/DMyVersion=$version" installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

$out = Join-Path $root "dist\VoxLupine-Setup-$version.exe"
Write-Host ("Done: {0} ({1:N1} MB)" -f $out, ((Get-Item $out).Length / 1MB))
