param(
    [string]$LocalAppDataRoot = $env:LOCALAPPDATA,
    [string]$HermesHome = "",
    [string]$HermesCommand = "hermes",
    [switch]$ConfirmHermesClosed,
    [switch]$RemoveData,
    [switch]$ConfirmDataDeletion
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
try {
    $bootstrap = (Get-Content -LiteralPath (Join-Path $PSScriptRoot "release-manifest.json") -Raw | ConvertFrom-Json)
    foreach ($name in @("uninstall.ps1", "InstallSupport.psm1")) {
        $entry = @($bootstrap.files | Where-Object { $_.path -ceq $name })
        $path = Join-Path $PSScriptRoot $name
        if ($entry.Count -ne 1 -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() `
            -cne $entry[0].sha256) { throw "bootstrap integrity" }
    }
    Import-Module (Join-Path $PSScriptRoot "InstallSupport.psm1") -Force
} catch {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    Write-Output '{"ok":false,"code":"UNINSTALL_VALIDATION_FAILED","message":"Package validation failed."}'
    exit 2
}

function Stop-HolUninstall([int]$ExitCode, [string]$Code, [string]$Message) {
    Write-HolResult ($ExitCode -eq 0) $Code $Message
    exit $ExitCode
}

if (-not $ConfirmHermesClosed) {
    Stop-HolUninstall 2 "HERMES_CLOSED_CONFIRMATION_REQUIRED" "Confirm Hermes is closed."
}
if ($RemoveData -and -not $ConfirmDataDeletion) {
    Stop-HolUninstall 2 "DATA_DELETION_CONFIRMATION_REQUIRED" "Confirm permanent data deletion."
}
if ([string]::IsNullOrWhiteSpace($LocalAppDataRoot)) {
    Stop-HolUninstall 2 "INSTALL_ROOT_INVALID" "Installation root is unavailable."
}
if ([string]::IsNullOrWhiteSpace($HermesHome)) {
    $HermesHome = Join-Path $LocalAppDataRoot "hermes"
}
$holonRoot = Join-Path $LocalAppDataRoot "Holon"
$appRoot = Join-Path $holonRoot "app"
$dataRoot = Join-Path $holonRoot "data"
$pluginRoot = Join-Path (Join-Path $HermesHome "plugins") "holon"

try {
    $oldHome = $env:HERMES_HOME
    try {
        $env:HERMES_HOME = $HermesHome
        $output = & $HermesCommand plugins disable holon 2>&1
        if ($LASTEXITCODE -ne 0) { throw [System.ArgumentException]::new("Hermes disable failed") }
    } finally { $env:HERMES_HOME = $oldHome }
    foreach ($path in @($appRoot, $pluginRoot)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    if ($RemoveData -and $ConfirmDataDeletion -and (Test-Path -LiteralPath $dataRoot)) {
        Remove-Item -LiteralPath $dataRoot -Recurse -Force
    }
    Stop-HolUninstall 0 "UNINSTALL_OK" "Holon program files were removed."
} catch [System.ArgumentException] {
    Stop-HolUninstall 2 "UNINSTALL_VALIDATION_FAILED" "Uninstall approval or compatibility failed."
} catch {
    Stop-HolUninstall 3 "UNINSTALL_FILESYSTEM_FAILED" "Uninstall could not be completed."
}
