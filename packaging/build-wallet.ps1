param(
    [string]$PythonPath = "",
    [string]$CompositionRoot = ""
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
$resourceRoot = Join-Path $sourceRoot "holon_wallet\resources"
$lendingReadProfile = Join-Path $sourceRoot "holon_lending\read-profiles.json"
$lendingActionProfile = Join-Path $sourceRoot "holon_lending\action-profiles.json"
$baselinePolicy = Join-Path $sourceRoot "holon_policy\baseline-policy.json"
$networkAssets = Join-Path $sourceRoot "holon_contracts\network-assets.json"
$defaultCompositionRoot = Join-Path $sourceRoot "holon_modules"
if ([string]::IsNullOrWhiteSpace($CompositionRoot)) {
    $CompositionRoot = $defaultCompositionRoot
}
$CompositionRoot = [IO.Path]::GetFullPath($CompositionRoot)
$moduleCatalog = Join-Path $CompositionRoot "module-catalog.json"
if (-not (Test-Path -LiteralPath $moduleCatalog -PathType Leaf)) {
    throw "Wallet module catalog is unavailable"
}
$moduleBuildArguments = @("--add-data", "$moduleCatalog;holon_modules")
$includeHyperliquidSdk = $false
$compositionModules = Join-Path $CompositionRoot "modules"
$catalog = Get-Content -LiteralPath $moduleCatalog -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($entry in @($catalog.modules)) {
    $stagedModuleRoot = Join-Path $compositionModules $entry.module_id
    $manifestPath = Join-Path $stagedModuleRoot "module-manifest.json"
    $moduleBuildArguments += @(
        "--add-data", "$manifestPath;holon_modules/modules/$($entry.module_id)"
    )
    $moduleManifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $entry.enabled) { continue }
    if ($entry.module_id -ceq "holon.perpdex") { $includeHyperliquidSdk = $true }
    foreach ($file in @($moduleManifest.files | Where-Object {
        $_.targets -ccontains "wallet" -or $_.targets -ccontains "shared"
    })) {
        $source = Join-Path $stagedModuleRoot $file.path.Replace("/", "\")
        $relativeParent = [IO.Path]::GetDirectoryName($file.path.Replace("/", "\"))
        $destination = "holon_modules/modules/$($entry.module_id)"
        if (-not [string]::IsNullOrWhiteSpace($relativeParent)) {
            $destination += "/" + $relativeParent.Replace("\", "/")
        }
        $moduleBuildArguments += @("--add-data", "$source;$destination")
    }
    $stagedSourceRoot = Join-Path $stagedModuleRoot "src"
    if (Test-Path -LiteralPath $stagedSourceRoot -PathType Container) {
        $moduleBuildArguments += @("--paths", $stagedSourceRoot)
    }
    foreach ($capability in @($moduleManifest.capabilities | Where-Object {
        $_.component -ceq "wallet" -and $null -ne $_.entry_point
    })) {
        $moduleBuildArguments += @("--hidden-import", $capability.entry_point.Split(":")[0])
    }
}
if ($includeHyperliquidSdk) {
    $moduleBuildArguments += @("--collect-all", "hyperliquid")
}
$versionFile = Join-Path $PSScriptRoot "windows-version.txt"
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$iconPath = Join-Path $buildRoot "holon-wallet.ico"
$previousPythonPath = $env:PYTHONPATH

try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $pythonVersion = & $PythonPath -c "import platform; print(platform.python_version())"
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.13.14") {
        throw "Wallet build requires CPython 3.13.14; found $pythonVersion"
    }
    & $PythonPath (Join-Path $PSScriptRoot "build_icon.py") `
        (Join-Path $qmlRoot "assets\holon.svg") $iconPath
    if ($LASTEXITCODE -ne 0) { throw "Wallet icon build failed" }
    & $PythonPath -m PyInstaller `
        --clean `
        --noconfirm `
        --onefile `
        --windowed `
        --noupx `
        --name HolonWallet `
        --version-file $versionFile `
        --icon $iconPath `
        --paths $sourceRoot `
        --add-data "$qmlRoot;holon_wallet/qml" `
        --add-data "$resourceRoot;holon_wallet/resources" `
        --add-data "$lendingReadProfile;holon_lending" `
        --add-data "$lendingActionProfile;holon_lending" `
        --add-data "$baselinePolicy;holon_policy" `
        --add-data "$networkAssets;holon_contracts" `
        @moduleBuildArguments `
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
    $policyArtifactRoot = Join-Path $distRoot "holon_policy"
    New-Item -ItemType Directory -Force -Path $policyArtifactRoot | Out-Null
    Copy-Item -LiteralPath $baselinePolicy `
        -Destination (Join-Path $policyArtifactRoot "baseline-policy.json") `
        -Force
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
