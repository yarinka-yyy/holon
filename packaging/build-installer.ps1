param(
    [string]$PythonPath = "",
    [string]$InnoCompilerPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Project Python is unavailable: $PythonPath"
}

$pythonVersion = & $PythonPath -c "import platform; print(platform.python_version())"
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.13.14") {
    throw "Installer build requires CPython 3.13.14; found $pythonVersion"
}

$buildRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "build\installer"))
$packageRoot = [System.IO.Path]::GetFullPath((Join-Path $buildRoot "package"))
$expectedPrefix = $buildRoot.TrimEnd('\') + '\'
if (-not $packageRoot.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe installer staging path"
}
if (Test-Path -LiteralPath $packageRoot) {
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

& (Join-Path $PSScriptRoot "build-guard.ps1") -PythonPath $PythonPath
if ($LASTEXITCODE -ne 0) { throw "Guard build failed" }
& (Join-Path $PSScriptRoot "build-wallet.ps1") -PythonPath $PythonPath
if ($LASTEXITCODE -ne 0) { throw "Wallet build failed" }

$iconPath = Join-Path $buildRoot "holon.ico"
& $PythonPath (Join-Path $PSScriptRoot "build_icon.py") `
    (Join-Path $projectRoot "src\holon_wallet\qml\assets\holon.svg") $iconPath
if ($LASTEXITCODE -ne 0) { throw "Installer icon build failed" }

& $PythonPath (Join-Path $PSScriptRoot "build_package.py") `
    --source-root $projectRoot `
    --destination $packageRoot `
    --guard (Join-Path $projectRoot "dist\HolonGuard.exe") `
    --wallet (Join-Path $projectRoot "dist\HolonWallet.exe")
if ($LASTEXITCODE -ne 0) { throw "Production package build failed" }

if ([string]::IsNullOrWhiteSpace($InnoCompilerPath)) {
    $pathCompiler = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $pathCompiler) {
        $InnoCompilerPath = $pathCompiler.Source
    }
    else {
        $candidates = @(
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
        )
        $InnoCompilerPath = $candidates | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_ -PathType Leaf)
        } | Select-Object -First 1
    }
}
if ([string]::IsNullOrWhiteSpace($InnoCompilerPath) -or
    -not (Test-Path -LiteralPath $InnoCompilerPath -PathType Leaf)) {
    throw "Official Inno Setup compiler (ISCC.exe) is required"
}

$distRoot = Join-Path $projectRoot "dist"
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
& $InnoCompilerPath `
    "/DPACKAGE_ROOT=$packageRoot" `
    "/DOUTPUT_DIR=$distRoot" `
    "/DLICENSE_FILE=$(Join-Path $projectRoot 'LICENSE')" `
    "/DSETUP_ICON=$iconPath" `
    (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

$setupPath = Join-Path $distRoot "Holon-0.1.0-alpha-Setup.exe"
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    throw "Installer artifact was not created"
}
Write-Output $setupPath
