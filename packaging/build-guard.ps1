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

$buildRoot = Join-Path $projectRoot "build\guard"
$distRoot = Join-Path $projectRoot "dist"
$sourceRoot = Join-Path $projectRoot "src"
$entryPoint = Join-Path $sourceRoot "holon_guard_app.py"
$qmlRoot = Join-Path $sourceRoot "holon_wallet\qml"
$lendingProfile = Join-Path $sourceRoot "holon_lending\read-profiles.json"
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
    throw "Guard module catalog is unavailable"
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
        $_.targets -ccontains "guard" -or $_.targets -ccontains "shared"
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
        $_.component -ceq "guard" -and $null -ne $_.entry_point
    })) {
        $moduleBuildArguments += @("--hidden-import", $capability.entry_point.Split(":")[0])
    }
}
if ($includeHyperliquidSdk) {
    $moduleBuildArguments += @("--collect-all", "hyperliquid")
}
$versionFile = Join-Path $PSScriptRoot "windows-version.txt"
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$previousPythonPath = $env:PYTHONPATH

try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $pythonVersion = & $PythonPath -c "import platform; print(platform.python_version())"
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.13.14") {
        throw "Guard build requires CPython 3.13.14; found $pythonVersion"
    }
    $pyInstallerExit = 1
    $pyInstallerErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $PythonPath (Join-Path $PSScriptRoot "run_pyinstaller.py") `
            --clean `
            --noconfirm `
            --onefile `
            --windowed `
            --noupx `
            --name HolonGuard `
            --version-file $versionFile `
            --paths $sourceRoot `
            --add-data "$lendingProfile;holon_lending" `
            --add-data "$lendingActionProfile;holon_lending" `
            --add-data "$baselinePolicy;holon_policy" `
            --add-data "$networkAssets;holon_contracts" `
            --add-data "$qmlRoot;holon_wallet/qml" `
            @moduleBuildArguments `
            --collect-data web3 `
            --distpath $distRoot `
            --workpath (Join-Path $buildRoot "work") `
            --specpath $buildRoot `
            $entryPoint
        $pyInstallerExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $pyInstallerErrorActionPreference
    }
    if ($pyInstallerExit -ne 0) {
        throw "PyInstaller failed with exit code $pyInstallerExit"
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
