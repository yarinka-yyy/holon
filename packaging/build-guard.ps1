param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Project Python is unavailable: $PythonPath"
}

$buildRoot = Join-Path $projectRoot "build\guard"
$distRoot = Join-Path $projectRoot "dist"
$sourceRoot = Join-Path $projectRoot "src"
$entryPoint = Join-Path $sourceRoot "holon_guard_app.py"
$lendingProfile = Join-Path $sourceRoot "holon_lending\read-profiles.json"
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$previousPythonPath = $env:PYTHONPATH

try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $pythonVersion = & $PythonPath -c "import platform; print(platform.python_version())"
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.13.14") {
        throw "Guard build requires CPython 3.13.14; found $pythonVersion"
    }
    & $PythonPath -m PyInstaller `
        --clean `
        --noconfirm `
        --onefile `
        --windowed `
        --noupx `
        --name HolonGuard `
        --paths $sourceRoot `
        --add-data "$lendingProfile;holon_lending" `
        --collect-data web3 `
        --distpath $distRoot `
        --workpath (Join-Path $buildRoot "work") `
        --specpath $buildRoot `
        $entryPoint
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    $artifact = Join-Path $distRoot "HolonGuard.exe"
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Guard artifact was not created"
    }
    Write-Output $artifact
}
finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPythonPath
    }
}
