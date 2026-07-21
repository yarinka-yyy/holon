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

$buildRoot = Join-Path $projectRoot "build\wallet"
$distRoot = Join-Path $projectRoot "dist"
$sourceRoot = Join-Path $projectRoot "src"
$entryPoint = Join-Path $sourceRoot "holon_wallet_app.py"
$qmlRoot = Join-Path $sourceRoot "holon_wallet\qml"
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$previousPythonPath = $env:PYTHONPATH

try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $pythonVersion = & $PythonPath -c "import platform; print(platform.python_version())"
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.13.14") {
        throw "Wallet build requires CPython 3.13.14; found $pythonVersion"
    }
    & $PythonPath -m PyInstaller `
        --clean `
        --noconfirm `
        --onefile `
        --windowed `
        --noupx `
        --name HolonWallet `
        --paths $sourceRoot `
        --add-data "$qmlRoot;holon_wallet/qml" `
        --collect-data bip_utils `
        --collect-all coincurve `
        --collect-data web3 `
        --hidden-import PySide6.QtQml `
        --hidden-import PySide6.QtQuick `
        --hidden-import PySide6.QtSvg `
        --distpath $distRoot `
        --workpath (Join-Path $buildRoot "work") `
        --specpath $buildRoot `
        $entryPoint
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    $artifact = Join-Path $distRoot "HolonWallet.exe"
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Wallet artifact was not created"
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
